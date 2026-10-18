# 2026-10-18 本地验证记录

结论：**PASS（打包硬件往返、真实服务安装、等效 PRESHUTDOWN 与真实关机/开机）**。基于协议修复提交 `6f3409b` 清洁构建，266 个自动化测试通过；打包 EXE 在唯一严格身份设备上完成 `1/1 → 0/0 → 1/1`，随后完成标准 UAC 安装、SCM 配置、服务启动、手动 STOP、等效 control 15 和真实 Windows 关机/下次开机验证。各阶段完整 `AmbientState` 的效果编号及 `0x01..0x0A` 全条目逐字节相同。最终服务 `RUNNING`、Automatic、LocalSystem，两路 `1/1`。

Task 10 初次安装因执行通道没有管理员令牌而退出 `1`，后由用户批准标准 UAC 安装成功，已解除阻塞。2026-10-18 19:29:02 至 19:29:08 +08:00 完成全部服务验收；`SvcOtherEx(15, 0, None)` 用时 `0.062s`，捕获 `STOP_PENDING` / waitHint `15000`，实际关灯读回成功后才置位 service stop event。文件日志与应用程序事件均记录成功。详见本文末尾记录。

## 环境与产物

| 项目 | 本次值 |
| --- | --- |
| 日期 / 时区 | 2026-10-18 / Asia/Shanghai |
| 构建源码提交 | `6f3409bf951d4238b776962cc3c8fd1e3e966212` |
| 项目版本 | `0.1.0` |
| 系统 / 架构 | Windows 11，`10.0.26300`，AMD64 |
| Python | `3.12.7`，Anaconda 发行版，64 位 |
| hidapi / pywin32 | `0.15.0` / `312` |
| pytest / PyInstaller | `7.4.4` / `6.20.0` |
| PyInstaller hooks | `2026.8` |
| 产物 | `dist/HaloSoundbarLightService.exe`，10,437,169 字节 |
| SHA-256 | `86c8ea9ae671187813df073f48cac09562eaeb727ce103892a09380dfa03519e` |

其余实际依赖版本：`altgraph 0.17.5`、`colorama 0.4.6`、`iniconfig 2.3.1`、`packaging 26.3`、`pefile 2024.8.26`、`pip 24.2`、`pluggy 1.7.0`、`pywin32-ctypes 0.2.3`、`setuptools 84.0.0`。没有修改依赖约束；直接依赖固定，传递依赖并非完整锁定。

## 清洁构建与校验

从项目根运行以下原构建命令，退出 `0`：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

沿用已验证可用的提升沙箱权限环境。脚本使用项目 `.venv` 与原 `requirements-build.txt`，先执行全套 pytest，再清理 `build` / `dist`，以 PyInstaller `--noconfirm --clean` 重建单文件 console EXE 和 SHA-256 文件。结果：`266 passed in 0.63s`，PyInstaller Analysis、PYZ、PKG、EXE 全部成功；构建前及校验时源码 HEAD 均为上表提交。

有 Anaconda 虚拟环境缺少 `conda-meta` 警告，以及 `grp`、`pwd`、`posix`、`resource`、`fcntl`、`_posixsubprocess`、`_frozen_importlib_external`、条件导入 `typing_extensions` / `_typeshed` 等 warnings；无构建失败或关键 HID/pywin32 隐藏导入缺失错误。不能表述为“零警告”。

独立执行以下命令退出 `0`，实际文件 SHA-256 与校验文件一致：

```powershell
Get-Content .\dist\HaloSoundbarLightService.exe.sha256
Get-FileHash .\dist\HaloSoundbarLightService.exe -Algorithm SHA256
```

## 严格身份与命令结果

只读枚举按现有 `_IDENTITY` 全字段匹配，精确匹配数为 `1`。下列身份由成功的 packaged `probe` 输出，未记录 HID path、序列号、用户名或本机绝对路径：

| 字段 | 结果 |
| --- | --- |
| 产品 | `EDIFIER Halo Soundbar Max` |
| VID / PID | `0x2D99 / 0xA073` |
| Interface | `4` |
| Usage page / usage | `0xFF14 / 0x0001` |
| 状态数组 | `0x0C` |

实际执行顺序与退出码：

| 顺序 | 命令/读取 | 退出码/结果 | 主音箱 / 低音炮 | effect |
| --- | --- | --- | --- | --- |
| 1 | `.\dist\HaloSoundbarLightService.exe probe` | `0` | `ON / ON` | `4` |
| 2 | venv 现有源码 `build_controller().probe()` 完整基线 | 成功 | `1 / 1` | `4` |
| 3 | `.\dist\HaloSoundbarLightService.exe off` | `0`，`SUCCESS: attempts=1 detail=` | 事务读回成功 | `4`，内部指纹不变 |
| 4 | 同一 EXE `probe`，随后源码完整快照 | `0` / 成功 | `OFF / OFF`；`0 / 0` | `4` |
| 5 | 同一 EXE `on` | `0`，`SUCCESS: attempts=1 detail=` | 事务读回成功 | `4`，内部指纹不变 |
| 6 | 同一 EXE `probe`，随后源码完整快照 | `0` / 成功 | `ON / ON`；`1 / 1` | `4` |
| 7 | 同一 EXE 最终额外 `probe` | `0` | `ON / ON` | `4` |

往返驱动命令为 PowerShell here-string 输入 `.\.venv\Scripts\python.exe -`，使用 `subprocess.run` 调用上述 EXE；该驱动进程退出 `0`。源码只读快照通过 `src` 导入路径调用现有 API；写入前已在命令输出中完整打印 `AmbientState` 与 `style_fingerprint`，每次读回亦完整打印。若关闭后任一步失败，驱动会有限重试两次 packaged `on`，必要时一次同源码 CLI `on` 并读回；本次全部成功，没有进入恢复分支。

## 完整快照与样式不变性

三次快照的 `array_index=12`、`effect_id=4`，以下六字节条目在三次快照中完全相同：

| index | 完整 `index R G B brightness speed` 十六进制 |
| --- | --- |
| `01` | `01 FF E6 FE 64 FF` |
| `02` | `02 01 00 00 64 08` |
| `03` | `03 00 00 00 64 FF` |
| `04` | `04 B4 76 FF 64 FF` |
| `05` | `05 01 00 00 64 FF` |
| `06` | `06 B1 39 FF 64 08` |
| `07` | `07 00 00 00 64 FF` |
| `08` | `08 00 00 00 64 FF` |
| `09` | `09 01 00 00 64 FF` |
| `0A` | `0A 00 00 00 64 FF` |

两条开关记录构成完整 `AmbientState` 的剩余部分：

| 阶段 | `0B` 主音箱完整条目 | `0C` 低音炮完整条目 |
| --- | --- | --- |
| BEFORE | `0B 00 00 01 FF FF` | `0C 00 00 01 FF FF` |
| AFTER_OFF | `0B 00 00 00 FF FF` | `0C 00 00 00 FF FF` |
| AFTER_ON | `0B 00 00 01 FF FF` | `0C 00 00 01 FF FF` |

指纹采用现有 `style_fingerprint(state)`，即 `(effect_id, tuple(entries[1..10]))`。另把效果字节与十条完整六字节记录连接为 61 字节，以保留可复核的逐字节证据。三次输出均为：

```text
0401FFE6FE64FF0201000064080300000064FF04B476FF64FF0501000064FF06B139FF64080700000064FF0800000064FF0901000064FF0A00000064FF
```

该 61 字节序列 SHA-256 三次均为 `c6bf1e23e9afef0a2539808528592db61c97de7165a2fbe897a8fa7ec136ee30`。比较直接使用对象元组与原始字节相等，哈希仅便于记录：

```text
before_equals_off=true
off_equals_on=true
before_equals_on=true
all_style_bytes_equal=true
FINAL_RECOVERY: BOTH_ON_VERIFIED; emergency recovery not needed
```

本次恢复执行期间三份额外离散快照也没有样式差异。更早的只读根因调查曾见 effect `03 → 09`，无法确定外部来源；它不属于本次往返，不归因给服务，也未发送任何恢复样式命令或操作 TempoHub。

## 历史阻塞与剩余范围

早先基于 `5a9984b` 的构建通过 244 个测试，但把发送帧 `2E AA ED` 用于解析真实接收帧 `2F BB EC`，导致 probe 返回 `invalid report header`；当时没有执行 off/on。根因调查取得独立原始 E3 报文，修复提交 `6f3409b` 将设备响应解析与主机发送校验分离，并增加真机字节 fixture 回归。本记录以修复后的重新构建和成功硬件往返作为最终结果，旧失败证据保留于任务报告。

上述 Task 9 阶段最终两路已恢复并读回为 `1/1`，证明打包交互命令与本次样式不变性。后续 Task 10 的服务验收和 Task 11 的真实关机/开机验收见下文；本段仅记录 Task 9 当时的范围。

## Task 10：管理员安装与等效 PRESHUTDOWN 验证

授权范围为安装、启停指定 Windows 服务及不关机的等效 PRESHUTDOWN 验证。未执行真实关机、系统重启、注销、样式命令或 TempoHub 操作。实际灯光写入仅通过已安装 EXE 与现有业务控制器执行固定电源开关事务。

安装前 Task 9 报告、校验文件与实际 dist EXE 的 SHA-256 一致；安装后再次独立核验固定 `%ProgramFiles%\HaloSoundbarLightService\HaloSoundbarLightService.exe`，仍为 `86c8ea9ae671187813df073f48cac09562eaeb727ce103892a09380dfa03519e`。产物 `10,437,169` 字节，严格身份匹配数 `1`。

初次工具提升通道仍为 `Elevated=False`，安装命令退出 `1`；当时服务和安装目录不存在。控制器随后通过用户批准的标准 `Start-Process powershell.exe -Verb RunAs -Wait` 运行原 `scripts/install.ps1`，报告 `ELEVATED_INSTALL_EXIT=0`。恢复验收进程独立输出 `ELEVATED_TOKEN: true`，并核验真实 SCM 与安装产物；未修改安装器或绕过 UAC。

验收用一次 UAC 提升执行本任务被忽略目录内的临时 PowerShell 包装脚本，包装脚本使用项目 venv 执行一次性 Python 驱动，退出 `0`。驱动通过现有 API 读完整状态；通过固定已安装 EXE 执行 `off` / `probe`，通过 PowerShell 执行指定服务的 `Restart-Service`、`Stop-Service`、`Start-Service`。每条子命令均核对退出码，真实灯光读回与样式检查全部通过；没有进入紧急恢复分支。

初始、Restart 后和最终 SCM 查询均通过，实际配置如下：

| 项目 | 实际结果 |
| --- | --- |
| 服务状态 / 类型 | `RUNNING=4` / `SERVICE_WIN32_OWN_PROCESS=16` |
| 启动类型 / 账户 | `Automatic=2` / `LocalSystem` |
| ImagePath | 带引号的 `"%ProgramFiles%\HaloSoundbarLightService\HaloSoundbarLightService.exe"`，与固定绝对路径直接比较一致 |
| failure actions | `SC_ACTION_RESTART=1`，三次各 `60000ms`；ResetPeriod `86400s`；无 reboot message / command |
| PreshutdownTimeout | `15000ms` |
| ControlsAccepted | `261 (0x105)`，包含 `SERVICE_ACCEPT_PRESHUTDOWN (0x100)` |
| Win32 / service-specific exit code | 均 `0` |

实际顺序与证据：

| 时间（+08:00） | 命令 / 阶段 | 退出码 / 结果 | 主音箱 / 低音炮 |
| --- | --- | --- | --- |
| 19:29:02 | 初始 SCM、完整快照、installed EXE `probe` | `0`，RUNNING | `1 / 1` |
| 19:29:03–04 | off 前完整快照；installed EXE `off`、`probe`；off 后完整快照 | 全部 `0`，`SUCCESS: attempts=1` | `0 / 0` |
| 19:29:05 | `Restart-Service`，等待启动事务；完整快照、installed EXE `probe`、SCM | 全部 `0`，RUNNING | `1 / 1` |
| 19:29:06–07 | STOP 前快照；`Stop-Service`；STOPPED SCM；立即 installed EXE `probe` 和完整快照 | 全部 `0`，完整状态对象前后相等 | `1 / 1` |
| 19:29:07–08 | control 15 前快照；等效调用；事务 before/after 与额外 probe/完整快照 | SUCCESS；服务仍 STOPPED | `0 / 0` |
| 19:29:08 | `Start-Service`，等待启动事务；完整快照、installed EXE `probe`、最终 SCM | 全部 `0`，RUNNING | `1 / 1` |

手动 STOP 的读回发生在后续 control 15 和 Start-Service 之前，独立输出 `MANUAL_STOP_STATE_UNCHANGED: true`；没有将后续启动开灯当成 STOP 不写灯的证据。

等效控制使用 `object.__new__(HaloSoundbarLightService)`，设置真实 `_stop_event` / `_cancel_event` / `ServiceLifecycle(build_controller, ...)` / `configure_logging()`；仅以捕获函数替换实例的 `ReportServiceStatus`，并调用 `SvcOtherEx(15, 0, None)`。停止事件用 `threading.Event` 子类观察 `set` 时刻，既没有替换关灯事务，也没有向 SCM 发送关机控制。实际耗时 `0.062s`：

```text
ReportServiceStatus: state=3 (SERVICE_STOP_PENDING), waitHint=15000
at ReportServiceStatus: stop_event=false, off_result_published=false
before stop_event.set: stop_event=false, off_result_published=true,
                      off_status=SUCCESS, off_after_switches=(0,0)
after stop_event.set: stop_event=true
```

十一份完整快照均为 `array_index=12`、`effect_id=4`，每份均输出完整 `AmbientState`、`style_fingerprint` 和规范字节，并直接比较指纹元组。`01..0A` 全部条目一直为：

```text
01 FF E6 FE 64 FF
02 01 00 00 64 08
03 00 00 00 64 FF
04 B4 76 FF 64 FF
05 01 00 00 64 FF
06 B1 39 FF 64 08
07 00 00 00 64 FF
08 00 00 00 64 FF
09 01 00 00 64 FF
0A 00 00 00 64 FF
```

每条顺序为 `index R G B brightness speed`。两次关灯后的 `0B / 0C` 分别为 `0B 00 00 00 FF FF` / `0C 00 00 00 FF FF`；其余阶段均为 `0B 00 00 01 FF FF` / `0C 00 00 01 FF FF`。效果字节与 `01..0A` 全记录组成的 61 字节序列在所有快照中均为：

```text
0401FFE6FE64FF0201000064080300000064FF04B476FF64FF0501000064FF06B139FF64080700000064FF0800000064FF0901000064FF0A00000064FF
```

SHA-256 均为 `c6bf1e23e9afef0a2539808528592db61c97de7165a2fbe897a8fa7ec136ee30`，与安装前和 Task 9 基线也相同。最终通过 `switches == (1, 1)`、SCM RUNNING / Automatic / 全配置、全部指纹元组相等断言，输出 `TASK10_RESULT: "PASS"`。正常 Start-Service 恢复成功，无需紧急 on 恢复。

`%ProgramData%\HaloSoundbarLightService\service.log` 实际成功记录：

```text
2026-10-18 19:25:32,098 INFO startup: status=SUCCESS attempts=0 detail=
2026-10-18 19:29:05,405 INFO startup: status=SUCCESS attempts=1 detail=
2026-10-18 19:29:07,442 INFO preshutdown: status=SUCCESS attempts=1 detail=
2026-10-18 19:29:08,771 INFO startup: status=SUCCESS attempts=1 detail=
```

Windows Application 事件日志也实际读到三条 Event ID `255`：`19:29:05`、`19:29:08` 为 source `HaloSoundbarLightService` 的 startup SUCCESS；`19:29:07` 为 source `Python Service` 的 preshutdown SUCCESS。等效调用运行于一次性 Python 进程，其默认事件源与已安装服务不同，结果均已记录；这不是 SCM 实际投递 PRESHUTDOWN 的证据。

卸载仅做静态核验。管理员在仓库运行 `powershell -ExecutionPolicy Bypass -File .\scripts\uninstall.ps1`；仓库不可用时按 [README 的已安装 EXE 回退命令](../../README.md#卸载与回滚) 执行固定路径 EXE 的 `uninstall`，检查退出 `0` 与 `sc.exe query HaloSoundbarLightService` 返回 `1060`，然后删除经过路径与联接检查的精确 `%ProgramFiles%\HaloSoundbarLightService` 目录。两种方式都保留 `%ProgramData%\HaloSoundbarLightService` 日志，本次未实际卸载或删除文件。

最终状态：已安装服务保留 `RUNNING`、Automatic、LocalSystem，两路灯光 `1/1`、effect `4`，完整样式与基线一致。Task 10 无遗留阻塞；没有实际卸载。

## Task 11：真实关机/开机验收

用户在观察等效关机和等效开机均正常后，明确回复“继续”授权真实关机。执行标准 Windows `shutdown.exe /s /t 30` 流程，没有使用强制关机参数。用户观察到关机前两路灯熄灭，手动开机进入 Windows 后两路灯自动亮起，并确认“表现正常”。

开机后于 `2026-10-18 19:52:53 +08:00` 使用只读提权检查完成独立读回：

| 项目 | 结果 |
| --- | --- |
| 服务状态 | `Running` |
| 启动类型 / 账户 | `Auto` / `LocalSystem` |
| 严格设备身份 | `VID 2D99` / `PID A073` / interface `4` / usage page `FF14` / usage `1` |
| 主音箱 / 低音炮 | `ON / ON`，完整状态 `1/1` |
| effect | `4` |
| 样式指纹 SHA-256 | `c6bf1e23e9afef0a2539808528592db61c97de7165a2fbe897a8fa7ec136ee30` |
| 与 Task 9/10 基线比较 | `STYLE_MATCH=true` |

服务文件日志提供了真实 SCM 关机通知与下次自动启动的成对证据：

```text
2026-10-18 19:48:41,325 INFO preshutdown: status=SUCCESS attempts=1 detail=
2026-10-18 19:49:42,118 INFO startup: status=SUCCESS attempts=1 detail=
```

Windows Application 事件日志的独立读回也存在同一对 Event ID `255`，Provider 均为 `HaloSoundbarLightService`：`19:48:41.328 +08:00` 的 message 为 `preshutdown: status=SUCCESS attempts=1`，`19:49:42.122 +08:00` 的 message 为 `startup: status=SUCCESS attempts=1`。事件日志与文件日志的时间、状态和尝试次数一致。

关机过程未观察到异常阻塞；`preshutdown` 成功记录发生在系统停机前，下次启动的 `startup` 成功记录与服务自动运行、硬件 `1/1` 读回相互佐证。此阶段未执行卸载、样式写入或 TempoHub 操作。最终服务保持 `Running`，两路灯保持开启。

# 2026-10-18 本地验证记录

结论：**PASS（打包产物与真实设备开关往返）**。基于协议修复提交 `6f3409b` 清洁构建，266 个自动化测试通过；打包 EXE 在唯一严格身份设备上完成 `1/1 → 0/0 → 1/1`。写入前、关闭后、开启后三次完整 `AmbientState` 的效果编号及 `0x01..0x0A` 全条目逐字节相同，最终额外 `probe` 确认两路 `ON`。本次未安装/启动服务，也未关机或重启，不包含 SCM 与关机/下次启动验收。

后续 Task 10：**BLOCKED（安装执行环境没有管理员令牌）**。即使使用工具的提升权限执行通道，预检仍为 `Elevated=False`；原安装命令退出 `1`，提示必须使用管理员 PowerShell。服务与固定安装目录均未创建，SCM 配置、服务启动、手动 STOP 和等效 control 15 未进入验收。2026-10-18 19:21:24 +08:00 的最终只读核验确认两路 `1/1`、effect `4`、完整样式指纹保持基线。详见本文末尾的安装尝试记录。

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

最终两路已恢复并读回为 `1/1`。本次只证明指定 EXE 在本机本设备上的交互探测、开关往返与此次样式不变性；服务安装、SCM 权限/自动启动、PRESHUTDOWN、真实关机与下一次启动尚未进行验收。

## Task 10：管理员安装尝试与阻塞证据

授权范围为安装 Windows 服务及不关机的等效 PRESHUTDOWN 验证；真实关机或重启未授权。本节未执行关机、重启、注销、任何灯光写入或 TempoHub 操作。

安装前再次对比 Task 9 报告、校验文件与实际 EXE：三者 SHA-256 均为 `86c8ea9ae671187813df073f48cac09562eaeb727ce103892a09380dfa03519e`，实际长度 `10,437,169` 字节。严格身份匹配数 `1`。只读 SCM 查询在安装前、失败后均返回 `1060`（服务不存在）。固定 `%ProgramFiles%\HaloSoundbarLightService` 目录和其中的 EXE 在两次检查中均不存在。

| 命令 / 检查 | 退出码 / 实际结果 |
| --- | --- |
| `Get-FileHash .\dist\HaloSoundbarLightService.exe -Algorithm SHA256`，对比 Task 9 与 `.sha256` | `0`，全部一致 |
| venv 只读预检：`OpenSCManager` / `OpenService`、精确枚举、`build_controller().probe()`、`style_fingerprint` | `0`，服务不存在、匹配 `1`、两路 `1/1`、effect `4`；完整状态已输出保存 |
| 管理员令牌预检 `WindowsPrincipal.IsInRole(Administrator)` | `False`；提升权限执行通道不等同于 Windows 管理员令牌 |
| `powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1`，通过提升权限执行通道 | `1`，`Run this script in an elevated administrator PowerShell.` |
| 安装/卸载包装脚本 `Parser.ParseFile` 静态语法检查 | `0`，两者均 `errors=0`；未执行卸载 |
| 最终 `.\dist\HaloSoundbarLightService.exe probe` | `0`，两路 `ON`、effect `4` |
| 最终 venv 完整快照、指纹与基线对比 | `0`，两路 `1/1`、样式字节相同 |

安装脚本在管理员检查处退出，早于创建目录、复制 EXE、注册和启动服务。没有尝试替换身份、绕过权限、修改安装器或进行 UAC 自动操作。错误输出仅保留错误正文，不记录本机工作目录或用户名。

SCM 验收项目的实际状态如下，不能用安装器源码中的预期值冒充当前配置：

| 项目 | 当前证据 |
| --- | --- |
| 服务状态 | 不存在，`OpenService` 错误 `1060` |
| RUNNING / Automatic / LocalSystem / ImagePath | 未注册，均无法查询或验收 |
| failure actions / PreshutdownTimeout=15000 / ControlsAccepted 含 PRESHUTDOWN | 未注册，均未验收 |
| installed EXE `off` → `Restart-Service` → `1/1` | 未执行；已安装 EXE 不存在 |
| 手动 STOP 不写灯 | 未执行；服务不存在 |
| `SvcOtherEx(15, 0, None)` 等效调用、STOP_PENDING / waitHint、完成后 stop event | 未执行；安装前置条件失败 |
| 最终 Start-Service / RUNNING | 未执行；实际最终状态是服务不存在、灯光 `1/1` |

完整只读快照 `PRE_INSTALL` 与 `FINAL_BLOCKED` 均为 `array_index=12`、`effect_id=4`；全部十二条记录如下，两次逐字节相同。每条完整顺序是 `index R G B brightness speed`：

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
0B 00 00 01 FF FF
0C 00 00 01 FF FF
```

两次均完整输出 `AmbientState` 和 `style_fingerprint`；效果字节与 `01..0A` 全记录组成的 61 字节序列为：

```text
0401FFE6FE64FF0201000064080300000064FF04B476FF64FF0501000064FF06B139FF64080700000064FF0800000064FF0901000064FF0A00000064FF
```

SHA-256 均为 `c6bf1e23e9afef0a2539808528592db61c97de7165a2fbe897a8fa7ec136ee30`。最终断言 `switches == (1, 1)`、基线指纹一致均通过，输出 `FINAL_BASELINE_STYLE_MATCH: true`。本节没有发出 off，不需要恢复写入。

日志：既有 `%ProgramData%\HaloSoundbarLightService\service.log` 为 `1,221` 字节，最终现场读取时 LastWrite 为 `2026-10-18 19:07:00 +08:00`，早于本次安装尝试；尾部两条历史记录为 `19:06:58,855 INFO SUCCESS: attempts=1 detail=` 与 `19:07:00,423 INFO SUCCESS: attempts=1 detail=`。本次安装没有进入 EXE，没有新增服务启动或 preshutdown 文件日志；Windows 事件日志中的服务路径尚未实测，不报告其成功。

卸载仅做静态核验。管理员在仓库运行 `powershell -ExecutionPolicy Bypass -File .\scripts\uninstall.ps1`；仓库不可用时按 [README 的已安装 EXE 回退命令](../../README.md#卸载与回滚) 执行固定路径 EXE 的 `uninstall`，检查退出 `0` 与 `sc.exe query HaloSoundbarLightService` 返回 `1060`，然后删除经过路径与联接检查的精确 `%ProgramFiles%\HaloSoundbarLightService` 目录。两种方式都保留 `%ProgramData%\HaloSoundbarLightService` 日志，本次未实际卸载或删除文件。

待续：必须在真正具有管理员令牌的执行环境中，从原安装命令重新进行 Task 10；随后按原步骤验证 SCM、服务启动、STOP 与等效 control 15，最后恢复服务 RUNNING 和灯光 `1/1`。真实关机和下一次系统启动仍属于单独授权的 Task 11，不能据本次阻塞记录推断通过。

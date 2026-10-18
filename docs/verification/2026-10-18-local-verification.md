# 2026-10-18 本地验证记录

结论：**BLOCKED（真实设备协议探测失败）**。清洁构建和 244 个自动化测试通过；唯一精确身份设备存在，但无法取得通过协议校验的完整 `AmbientState`。未运行 `off`、`on` 或任何样式设置，设备开关最终状态未知，本任务未改变灯光。未安装或启动服务，也未执行关机或重启。

## 环境与产物

| 项目 | 本次值 |
| --- | --- |
| 日期 / 时区 | 2026-10-18 / Asia/Shanghai |
| 源码提交 | `5a9984b36aa3c754b531cc657316dbbfa52c22e5` |
| 项目版本 | `0.1.0` |
| 系统 / 架构 | Windows 11，`10.0.26300`，AMD64 |
| Python | `3.12.7`，Anaconda 发行版，64 位 |
| hidapi / pywin32 | `0.15.0` / `312` |
| pytest / PyInstaller | `7.4.4` / `6.20.0` |
| PyInstaller hooks | `2026.8` |
| 产物 | `dist/HaloSoundbarLightService.exe`，10,436,899 字节 |
| SHA-256 | `deddb50987d2bb46fa1d1f3dcb810f257d5db42bc5bbf0d64a414a6e6cf08971` |

其余实际依赖版本：`altgraph 0.17.5`、`colorama 0.4.6`、`iniconfig 2.3.1`、`packaging 26.3`、`pefile 2024.8.26`、`pip 24.2`、`pluggy 1.7.0`、`pywin32-ctypes 0.2.3`、`setuptools 84.0.0`。构建没有修改依赖约束；直接依赖固定，传递依赖并非完整锁定。

## 自动化测试和清洁构建

从项目根运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

首次沙箱内执行退出 `1`：创建虚拟环境时，`ensurepip --upgrade --default-pip` 子进程退出 `1`，构建未进入测试或打包。未通过修改依赖版本绕过。

以提升的沙箱权限重跑同一构建命令，最终退出 `0`。脚本创建项目 `.venv`，安装 `requirements-build.txt`，先执行全套 pytest，然后清理项目 `build` / `dist`，用 PyInstaller `--noconfirm --clean` 重建 console 单文件 EXE 并生成 SHA-256 文件。测试结果：`244 passed in 0.65s`。

PyInstaller 成功完成 Analysis、PYZ、PKG 和 EXE；`hid`、`win32timezone`、`win32event`、`pythoncom`、`win32security` 隐藏导入被分析，没有构建失败或关键隐藏导入缺失错误。构建仍有警告：基础 Python 为 Anaconda，虚拟环境不存在 `conda-meta`；warnings 文件列出 `grp`、`pwd`、`posix`、`resource`、`fcntl`、`_posixsubprocess`、`_frozen_importlib_external`、条件导入的 `typing_extensions` / `_typeshed` 等条目。不能将本次成功构建表述为“零警告”或服务安装验收。

独立核对命令退出 `0`，EXE 实际哈希与校验文件一致：

```powershell
Get-Content .\dist\HaloSoundbarLightService.exe.sha256
Get-FileHash .\dist\HaloSoundbarLightService.exe -Algorithm SHA256
```

## 设备身份与只读探测

打包入口命令在普通沙箱和提升沙箱权限环境各执行一次，两次退出码均为 `1`：

```powershell
.\dist\HaloSoundbarLightService.exe probe
```

两次输出均为：

```text
probe: No matching Halo interface passed the state probe
```

随后只读调用 `hid.enumerate(0x2D99, 0xA073)`，只输出非路径身份字段。枚举命令退出 `0`，共三个 collection：

| 产品 | VID / PID | 接口 | Usage page / usage | 精确匹配 |
| --- | --- | --- | --- | --- |
| EDIFIER Halo Soundbar Max | `0x2D99 / 0xA073` | `4` | `0xFF13 / 0x0001` | 否 |
| EDIFIER Halo Soundbar Max | `0x2D99 / 0xA073` | `4` | `0xFF14 / 0x0001` | 是，唯一 |
| EDIFIER Halo Soundbar Max | `0x2D99 / 0xA073` | `3` | `0x000C / 0x0001` | 否 |

没有记录序列号、完整 HID path、用户名或本机绝对路径。身份字段唯一匹配不代表协议探测成功。

使用项目 `.venv` 的 Python，按现有 `_IDENTITY` 全字段匹配并调用 `DeviceClient.connect` / `query_state`。该只读诊断退出 `1`，输出如下：

```text
Exact identity matches: 1
DeviceNotFoundError: No matching Halo interface passed the state probe
DeviceProtocolError: No valid state before deadline: invalid report header
ProtocolError: invalid report header
```

源码与 EXE 均未取得可信快照。已定位到 HID 返回报文进入现有协议解析的边界，具体报文差异与根因尚未确认；本任务没有放宽身份筛选、修改协议或绕过校验。

## 开关往返与样式证据

| 阶段 | 主音箱 `0x0B` | 低音炮 `0x0C` | effect / `0x01..0x0A` | 结果 |
| --- | --- | --- | --- | --- |
| 写入前 EXE probe / 源码完整快照 | 未知 | 未知 | 无可信快照，无法计算指纹 | 阻塞 |
| EXE off → probe / 完整快照 | 未执行 | 未执行 | 未执行 | 安全前提未满足 |
| EXE on → probe / 完整快照 | 未执行 | 未执行 | 未执行 | 安全前提未满足 |

目标是比较三次 `style_fingerprint(state)`，其内容为效果编号与索引 `0x01..0x0A` 全部 `LightEntry(index, red, green, blue, brightness, speed)`；只有这些值逐字节相同，且关闭后两开关 `0/0`、打开后 `1/1`，才可宣告往返通过。本次第一份快照就失败，故没有指纹或比较通过证据。

没有执行关灯，无须执行恢复开灯；未重试 packaged `on`，也未使用 source CLI `on`。最终状态为“灯光未被本任务改变、两开关当前值未知”，不能写为“已恢复为 `1/1`”。

## 剩余验收

需先解决并重新验证只读报文解析，再重新执行完整快照、EXE off、关闭后双重读回、EXE on、开启后双重读回及三次样式逐字节比较。若关闭后任何步骤失败，应优先有限重试 packaged `on`，必要时使用同一源码 CLI `on` 恢复，再记录最终实际开关状态。本次不包含服务安装、SCM 启停、真实关机和下次启动验收。

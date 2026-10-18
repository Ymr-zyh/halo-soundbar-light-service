# Halo Soundbar 氛围灯服务

Windows 服务负责在系统启动时打开主音箱与低音炮氛围灯，并在关机预通知时关闭灯光。仅修改电源开关；尽量保留原有效果、颜色、亮度与速度。每次操作均查询当前状态并核验结果；并发修改或设备异常可能导致操作失败，应查看退出码和日志。

> **真实关机测试必须另行获得人工确认，并先保存全部工作。自动化测试、构建和安装步骤不会主动关机；不要把关机命令加入测试或脚本。**

## 支持的精确设备身份

所有字段必须同时匹配，并且设备必须通过协议状态探测；不会仅凭 VID/PID 选设备。多个匹配设备均响应时会拒绝操作。

| 字段 | 值 |
| --- | --- |
| 产品字符串 | `EDIFIER Halo Soundbar Max`（大小写完全一致） |
| VID / PID | `0x2D99` / `0xA073` |
| HID interface | `4` |
| Usage page / usage | `0xFF14` / `0x0001` |
| Report ID / 状态数组 | `0x2E` / `0x0C` |
| 控制目标 | 主音箱 `0x0B`、低音炮 `0x0C` |

## 构建

在 Windows 上安装 Python 3.12 或更新版本（建议固定使用相同 Python 小版本、架构和系统构建），从项目根运行：

```powershell
.\scripts\build.ps1
```

脚本使用当前 `python` 创建或复用项目内 `.venv`，后续 pip、pytest、PyInstaller 全部使用 `.venv\Scripts\python.exe`。依赖版本固定在 `requirements-build.txt`；脚本先跑全套测试，成功后才清理项目内 `build` 与 `dist` 并构建。脚本拒绝上述输出目录为联接或符号链接。不要在 build/dist 中存放需保留的数据。

产物是单文件 console EXE `dist\HaloSoundbarLightService.exe` 与 `dist\HaloSoundbarLightService.exe.sha256`。spec 以 `src\halo_soundbar_service\__main__.py` 为入口，包含 pywin32 服务组件与 hidapi 隐藏导入。console 模式保留交互命令输出；由 SCM 无参数启动时进入服务分派器。SHA-256 用于核对文件，不代表数字签名。固定依赖支持重建流程；不同 Python、Windows 或工具链环境不保证逐字节相同。

```powershell
Get-Content .\dist\HaloSoundbarLightService.exe.sha256
Get-FileHash .\dist\HaloSoundbarLightService.exe -Algorithm SHA256
```

只运行单测（不接触真实 HID 或 SCM）：

```powershell
New-Item -ItemType Directory -Path .\build -Force | Out-Null
python -m pytest -v --basetemp .\build\pytest
```

## 安装前验证与命令

先连接设备，在交互式 PowerShell 中依次执行 **probe → off → on**，每步成功且观察结果正确后才继续；失败应立即停止安装。`probe` 查询设备和灯光状态，不修改灯光电源。

```powershell
.\dist\HaloSoundbarLightService.exe probe
.\dist\HaloSoundbarLightService.exe off
.\dist\HaloSoundbarLightService.exe on
```

每次改变电源后再次执行 `probe` 并检查 `$LASTEXITCODE`：关闭后主音箱与低音炮都应为 `OFF`，重新打开后都应为 `ON`。若初次 `probe` 失败，不要执行 `off`。若关闭后的核验或重新打开失败，应优先有限重试 `on` 恢复亮灯，再排查失败原因。

完整样式不变性验收还需在写入前、关闭后和打开后，通过项目虚拟环境调用现有只读 API，比较效果编号及 `0x01..0x0A` 每个条目的全部六字节；CLI 的 `probe` 摘要本身不能证明样式未变：

```powershell
@'
import sys
sys.path.insert(0, 'src')
from halo_soundbar_service.service import build_controller
from halo_soundbar_service.protocol import style_fingerprint
state = build_controller().probe()
print('AmbientState:', state)
print('style_fingerprint:', style_fingerprint(state))
'@ | .\.venv\Scripts\python.exe -
$LASTEXITCODE
```

2026-10-18 本地验证通过：266 个自动化测试、打包 EXE 真实设备开关往返、标准 UAC 管理员安装、SCM 配置、服务启动开灯、手动 STOP 保持灯光，以及不关机的等效 `SvcOtherEx(15, 0, None)` 关灯路径。最终服务 `RUNNING`、Automatic、LocalSystem，两路灯光 `1/1`；各阶段完整快照的效果编号与 `0x01..0x0A` 全条目逐字节相同。初次非管理员令牌的安装阻塞已由标准 UAC 解决；真实 Windows 关机前自动关灯及下次启动自动开灯也已于 2026-10-18 验收通过。详见 [本地验证记录](docs/verification/2026-10-18-local-verification.md#task-11真实关机开机验收)。

四条交互诊断命令如下；`off`、`on` 会实际改灯。

| 命令 | 行为 |
| --- | --- |
| `probe` | 精确身份探测，输出数组、效果和两路电源状态 |
| `off` | 关闭主音箱与低音炮氛围灯并读回核验 |
| `on` | 打开两路氛围灯并读回核验 |
| `debug` | 前台运行启动开灯逻辑，等待诊断；Ctrl+C 退出且不额外关灯 |

`debug` 不注册服务，也不模拟 Windows PRESHUTDOWN；请先停止已安装服务，避免与自身并发执行。所有命令成功退出码为 `0`；设备缺失、协议异常、核验失败、权限或 SCM 错误等为非零（通常 `1`），参数错误为 `2`。普通命令被 Ctrl+C 中断时为 `130`；`debug` 正常等待期间 Ctrl+C 退出时返回启动操作结果（成功 `0`、失败 `1`）。必须检查退出码，看到进程结束不能视为成功。

```powershell
.\dist\HaloSoundbarLightService.exe debug
$LASTEXITCODE
```

## 管理员安装与服务行为

完成上述验证后，以管理员身份打开 PowerShell，在项目根运行：

```powershell
.\scripts\install.ps1
# 可指定另一个已验证的 EXE：
.\scripts\install.ps1 -SourceExe 'D:\Release\HaloSoundbarLightService.exe'
```

脚本复制到 `%ProgramFiles%\HaloSoundbarLightService\HaloSoundbarLightService.exe`，调用其 `install` 命令注册服务，再启动并检查 Running。安装失败返回非零；已存在服务时拒绝覆盖，请先按卸载流程处理。若注册成功但启动失败，保留 EXE 与服务以供诊断，不会报告安装成功。

服务名为 `HaloSoundbarLightService`，显示名为 `EDIFIER Halo SoundBar Light Service`，以 LocalSystem 自动启动。启动阶段最多等待设备约 120 秒。失败恢复策略为间隔 60 秒重启，重置周期一天。系统 PRESHUTDOWN 预通知注册超时 15000 毫秒，关灯事务总预算约 12 秒；普通 SHUTDOWN 是幂等回退。手动 `Stop-Service` 或卸载所用的 STOP 只停止服务，不改变灯光；重新 Start-Service 会执行开灯。

EXE 的 `install` 与 `uninstall` 是底层管理员命令：`install` 只注册服务且必须从冻结 EXE 运行；`uninstall` 停止并移除注册且保留日志。推荐使用包装脚本管理固定安装路径。源码入口可用于诊断：`python -m halo_soundbar_service probe`（需先安装运行依赖并设置 `PYTHONPATH=src` 或使用可编辑安装）。

## TempoHub 共存与故障排查

服务按操作打开并及时关闭 HID，不常驻独占句柄，不要求禁用 TempoHub。操作前后查询状态，检测效果或其他风格字段被并发修改时会中止或重试；不能保证与 TempoHub 同时改灯一定成功。先等待 TempoHub 停止调整后重试；若仍失败，可临时关闭 TempoHub 排查，避免两边同时连续操作。

文件日志位于 `%ProgramData%\HaloSoundbarLightService\service.log`，UTF-8，单文件最多 1 MiB，保留 3 份轮转备份；同时尽力写入 Windows“应用程序”事件日志。文件日志无法写入时仍尝试事件日志。检查设备身份、USB 连接、管理员权限和命令错误输出，再检查日志；服务 Running 只表示服务进程在运行，不能代替灯光读回核验。

```powershell
Get-Service -Name HaloSoundbarLightService
Get-Content "$env:ProgramData\HaloSoundbarLightService\service.log" -Tail 80
```

## 卸载与回滚

以管理员身份运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall.ps1
```

脚本从 Windows Program Files Known Folder API 取得可信根目录，并在卸载前核对 SCM 中服务 `ImagePath` 与固定安装 EXE 完全一致。底层卸载成功并确认服务不存在后，只删除该 EXE 和空的固定安装目录，不执行递归删除。遇到意外目录内容、重解析点、删除待决、权限错误、丢失 EXE、ImagePath 不符或服务仍存在时返回非零并尽量保留文件；关闭占用服务的管理工具后排查重试。它不删除 ProgramData 日志、项目源码或其他目录。卸载不改变当前灯光，需要恢复亮灯时使用项目保留的 EXE 执行 `on`。

若仓库已不可用，在管理员 PowerShell 中运行以下已安装 EXE 回退命令。仅在卸载退出 `0`、`sc.exe query` 明确返回 `1060`（服务不存在），且精确安装目录通过检查后删除该目录；删除待决时保留文件并稍后重试：

```powershell
$ErrorActionPreference = 'Stop'
$KnownFolderProgramFiles = [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles)
if ([string]::IsNullOrWhiteSpace($KnownFolderProgramFiles) -or -not [IO.Path]::IsPathRooted($KnownFolderProgramFiles)) {
    throw 'The Windows Program Files known folder is invalid.'
}
$ProgramFilesDirectory = [IO.Path]::GetFullPath($KnownFolderProgramFiles)
if (-not (Test-Path -LiteralPath $ProgramFilesDirectory -PathType Container)) {
    throw 'The Windows Program Files known folder does not exist.'
}
$InstallDirectory = [IO.Path]::GetFullPath((Join-Path $ProgramFilesDirectory 'HaloSoundbarLightService'))
if (-not [StringComparer]::OrdinalIgnoreCase.Equals([IO.Path]::GetDirectoryName($InstallDirectory), $ProgramFilesDirectory)) {
    throw 'Installation directory is outside the Windows Program Files known folder.'
}
$InstalledExe = Join-Path $InstallDirectory 'HaloSoundbarLightService.exe'
foreach ($Target in @($InstallDirectory, $InstalledExe)) {
    $Item = Get-Item -LiteralPath $Target -Force
    if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'Refusing an installation reparse point; files were preserved.'
    }
}
$UnexpectedEntries = @(Get-ChildItem -LiteralPath $InstallDirectory -Force | Where-Object {
    -not [StringComparer]::OrdinalIgnoreCase.Equals($_.FullName, $InstalledExe)
})
if ($UnexpectedEntries.Count -ne 0) { throw 'Unexpected installation entries; files were preserved.' }
$Service = Get-CimInstance -ClassName Win32_Service -Filter "Name='HaloSoundbarLightService'" -ErrorAction Stop
$ExpectedImagePath = '"' + $InstalledExe + '"'
if ($null -eq $Service -or -not [StringComparer]::OrdinalIgnoreCase.Equals(([string]$Service.PathName).Trim(), $ExpectedImagePath)) {
    throw 'Registered service ImagePath does not match the fixed installed EXE; files were preserved.'
}
& $InstalledExe uninstall
if ($LASTEXITCODE -ne 0) { throw 'Service removal failed; files were preserved.' }
sc.exe query HaloSoundbarLightService
if ($LASTEXITCODE -ne 1060) { throw 'Service absence was not confirmed; files were preserved.' }
$Directory = Get-Item -LiteralPath $InstallDirectory -Force
if (($Directory.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw 'Refusing an installation reparse point; files were preserved.'
}
Remove-Item -LiteralPath $InstalledExe -Force
Remove-Item -LiteralPath $InstallDirectory -Force
```

两种方式均保留 `%ProgramData%\HaloSoundbarLightService` 下的日志。本次仅审阅卸载逻辑并检查脚本语法，没有实际卸载；已验证服务保留运行。

升级或回滚前保留已验证的旧 EXE 与 SHA-256：先卸载，再用 `install.ps1 -SourceExe` 安装目标版本，重新执行 probe/off/on 验证。本机真实关机与下一次启动行为已于 2026-10-18 验收通过；未来若重复真实关机测试，仍须另行获得人工确认并先保存全部工作。单测通过不能替代新环境上的硬件或关机验收。

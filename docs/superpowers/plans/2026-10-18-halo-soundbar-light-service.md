# Halo SoundBar Max 氛围灯服务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建并打包一个 Windows 自动启动服务，在系统启动后打开 Halo SoundBar Max 的两组氛围灯，并在 `SERVICE_CONTROL_PRESHUTDOWN` 阶段只关闭两组灯的电源开关而不改变灯效。

**Architecture:** 将 HID 协议、设备访问、开关事务和 Windows Service 生命周期分成独立模块；协议与事务层通过注入式后端测试，Windows 适配层保持薄封装。所有写入前后均执行严格设备识别和状态查询，以最终状态而非 ACK 判断成功。

**Tech Stack:** Python 3.12.7、pywin32 312、hidapi 0.15.0、pytest 7.4.4、PyInstaller 6.20.0、PowerShell 5.1+

**Spec:** `docs/superpowers/specs/2026-10-18-halo-soundbar-light-service-design.md`

## Global Constraints

- 仅支持 VID `0x2D99`、PID `0xA073`、接口 `4`、Usage Page `0xFF14`、Usage `0x0001`、产品名 `EDIFIER Halo Soundbar Max`。
- 仅允许命令 `0x6A` 查询，以及针对灯组 `0x0C`、开关索引 `0x0B`/`0x0C` 的固定 `0x6B` 开关写入。
- 报告固定 64 字节；`2E AA ED` 主机发送帧的校验和为从 `0xAA` 到载荷末尾的字节和模 256；`2F BB EC` 设备响应无该校验和，需校验声明长度和载荷后全零填充。
- 写入前必须完成协议探测；身份、灯组、条目结构或状态值不符合预期时不得写入。
- 启动设备枚举重试总计不超过 120 秒；PRESHUTDOWN 配置 15000 ms，内部关灯使用约 12 秒的尽力预算。不可取消的原生 HID 调用卡死时，SCM 的 15 秒超时是保证 Windows 继续关机的系统硬兜底。
- 手动 STOP 不改变灯光；PRESHUTDOWN 和 SHUTDOWN 执行幂等关灯。
- 不修改、终止或依赖 TempoHub，不写设备固件，不提供任意灯效写入接口。
- 使用测试驱动开发：每项功能先看到目标测试失败，再写最小实现使其通过。
- 真实关机/重启测试必须再次获得用户明确授权，不纳入无人值守命令。

## Review Focus

- 同一设备产生 ACK、旧 `0xE3` 包和新查询分包交错时，清空已排队响应并以新收集器获取完整一致状态；Task 2 用陈旧包与乱序包测试锁定。由于线路无 transaction ID，写出查询后才到达的同灯效旧分包存在不可消除的归属歧义；用户已接受该限制，命令白名单仍保证不写灯效字段。
- 两个完全匹配且都能通过协议探测的设备同时存在时拒绝写入；Task 3 用多设备歧义测试锁定。
- PRESHUTDOWN 与尚未完成的启动重试竞争时，关灯必须取得优先权且不能死锁；Task 5 用并发取消测试锁定。
- TempoHub 在本服务事务期间改变样式时，不把外部变化错误归因于开关命令，也不能发送“恢复样式”写入；Task 4 用并发样式变化测试锁定。
- 打包后的冻结进程由 SCM 无参数启动时必须进入服务分派，而用户命令仍进入 CLI；Task 7 用冻结入口分派测试与打包烟雾测试锁定。

---

## File Structure

```text
pyproject.toml                         项目元数据、运行依赖和 pytest 配置
requirements-build.txt                可复现构建依赖版本
src/halo_soundbar_service/__init__.py  包版本
src/halo_soundbar_service/protocol.py  HID 帧、状态条目和分包收集
src/halo_soundbar_service/device.py    设备枚举、严格匹配和 HID 会话
src/halo_soundbar_service/controller.py 开关事务、验证与重试
src/halo_soundbar_service/lifecycle.py 与 pywin32 解耦的服务生命周期协调
src/halo_soundbar_service/logging_setup.py 事件日志与滚动文件日志
src/halo_soundbar_service/service.py   pywin32 ServiceFramework 适配
src/halo_soundbar_service/installer.py 服务注册、配置和移除
src/halo_soundbar_service/cli.py       probe/on/off/install/uninstall/debug
src/halo_soundbar_service/__main__.py  冻结/源码入口分派
tests/fakes.py                         HID、时钟、控制器测试替身
tests/test_protocol.py                 帧与状态解析测试
tests/test_device.py                   设备发现与会话测试
tests/test_controller.py               开关事务测试
tests/test_lifecycle.py                生命周期竞争与幂等测试
tests/test_service.py                  Windows 适配层测试
tests/test_cli.py                      命令分派和返回码测试
tests/test_installer.py                SCM 配置参数测试
scripts/build.ps1                      清洁构建、测试、PyInstaller 和摘要
scripts/install.ps1                    提权检查和安装调用
scripts/uninstall.ps1                  提权检查和卸载调用
HaloSoundbarLightService.spec          PyInstaller 固定配置
README.md                              使用、诊断、安装、回滚和验证说明
```

### Task 1: 项目骨架与受限 HID 帧编码

**Files:**
- Create: `pyproject.toml`
- Create: `requirements-build.txt`
- Create: `src/halo_soundbar_service/__init__.py`
- Create: `src/halo_soundbar_service/protocol.py`
- Create: `tests/test_protocol.py`

**Interfaces:**
- Consumes: 无。
- Produces: `PowerTarget`、`ProtocolError`、`ParsedFrame`、`LightEntry`、`AmbientState`；`build_frame(command: int, payload: bytes) -> bytes`；`build_query_packet() -> bytes`；`build_power_packet(target: PowerTarget, enabled: bool) -> bytes`；`parse_frame(report: bytes) -> ParsedFrame`；`parse_response_frame(report: bytes) -> ParsedFrame`。

- [ ] **Step 1: 写四条固定开关帧和查询帧的失败测试**

  在 `tests/test_protocol.py` 添加 `test_build_power_packets_match_verified_device_frames`，断言四个 64 字节结果的前缀分别为 `2EAAED6B00070C0B000000FFFF1E`、`2EAAED6B00070C0B000001FFFF1F`、`2EAAED6B00070C0C000000FFFF1F`、`2EAAED6B00070C0C000001FFFF20`，其余字节全为零；添加查询帧长度、Report ID 和校验和断言。

- [ ] **Step 2: 运行协议编码测试并确认失败**

  Run: `python -m pytest tests/test_protocol.py -k "build_power or query" -v`
  Expected: FAIL，原因是 `halo_soundbar_service.protocol` 或目标符号尚不存在。

- [ ] **Step 3: 实现最小受限编码接口**

  在 `protocol.py` 定义 `PowerTarget(Enum)` 的 `MAIN=0x0B`、`SUBWOOFER=0x0C`，固定 `ARRAY_INDEX=0x0C` 和 `REPORT_SIZE=64`。`build_power_packet` 不接受任意索引，`build_frame` 拒绝命令范围、载荷过长和非 bytes 输入。

- [ ] **Step 4: 添加并实现基础帧解析验证**

  先添加 `test_parse_frame_rejects_bad_header_length_checksum_and_truncation`，确认失败；再实现 `parse_frame`，负责 `2E AA ED` 主机帧的帧头、声明长度和校验和检查。根据后续真机证据增加 `parse_response_frame`，负责无主机校验和的 `2F BB EC` 设备响应帧长度和全零填充检查；两者均返回不可变 `ParsedFrame(command: int, payload: bytes)`。

- [ ] **Step 5: 运行本任务测试**

  Run: `python -m pytest tests/test_protocol.py -v`
  Expected: 全部 PASS。

- [ ] **Step 6: 提交**

  ```bash
  git add pyproject.toml requirements-build.txt src/halo_soundbar_service/__init__.py src/halo_soundbar_service/protocol.py tests/test_protocol.py
  git commit -m "feat: add constrained Halo HID protocol"
  ```

### Task 2: `0xE3` 状态分包收集与陈旧响应隔离

**Files:**
- Modify: `src/halo_soundbar_service/protocol.py`
- Modify: `tests/test_protocol.py`

**Interfaces:**
- Consumes: Task 1 的 `parse_response_frame`、`ParsedFrame`、`LightEntry`、`AmbientState`。
- Produces: `AmbientStateCollector(expected_array_index: int = 0x0C)`；`feed(frame: ParsedFrame) -> AmbientState | None`；`reset() -> None`；`style_fingerprint(state: AmbientState) -> tuple[int, tuple[LightEntry, ...]]`。

- [ ] **Step 1: 写有效两分包重组失败测试**

  添加 `test_collector_reassembles_two_e3_packets_out_of_order`，使用两个合法 `0xE3` 测试帧，断言得到 `array_index=0x0C`、指定效果编号以及索引 `0x01` 至 `0x0C` 的映射。

- [ ] **Step 2: 运行单测确认失败**

  Run: `python -m pytest tests/test_protocol.py::test_collector_reassembles_two_e3_packets_out_of_order -v`
  Expected: FAIL，原因是 `AmbientStateCollector` 尚不存在。

- [ ] **Step 3: 实现分包收集器**

  `feed` 忽略非 `0xE3` 命令；校验总分包数固定为 2、序号只能为 1/2、灯组固定为 `0x0C`、条目长度必须为 6 的倍数、索引不能重复。只有同一效果编号的一组 1/2 包齐全时返回 `AmbientState`。

- [ ] **Step 4: 写陈旧、重复、缺包和不一致测试并完善实现**

  添加 `test_collector_discards_stale_generation_after_new_packet_one`、`test_collector_rejects_duplicate_entry_across_parts`、`test_collector_never_completes_with_missing_part`、`test_collector_rejects_mismatched_effect_or_array`，并为 `style_fingerprint` 断言只包含效果编号及索引 `0x01..0x0A` 的完整字段。

- [ ] **Step 5: 运行协议测试**

  Run: `python -m pytest tests/test_protocol.py -v`
  Expected: 全部 PASS。

- [ ] **Step 6: 提交**

  ```bash
  git add src/halo_soundbar_service/protocol.py tests/test_protocol.py
  git commit -m "feat: parse ambient state response packets"
  ```

### Task 3: 严格 HID 设备发现与有界会话

**Files:**
- Create: `src/halo_soundbar_service/device.py`
- Create: `tests/fakes.py`
- Create: `tests/test_device.py`

**Interfaces:**
- Consumes: Task 1/2 的 `build_query_packet`、`build_power_packet`、`parse_response_frame`、`AmbientStateCollector`、`AmbientState`、`PowerTarget`。
- Produces: `DeviceNotFoundError`、`AmbiguousDeviceError`、`DeviceProtocolError`、`ReadTimeoutError`；`HidBackend` Protocol；`DeviceClient.connect(backend: HidBackend, monotonic: Callable[[], float]) -> DeviceClient`；上下文管理；`drain(max_reads: int = 64, read_timeout_ms: int = 10) -> int`；`query_state(timeout_ms: int = 800) -> AmbientState`；`write_power(target: PowerTarget, enabled: bool) -> None`。

- [ ] **Step 1: 写严格身份筛选失败测试**

  添加 `test_connect_ignores_wrong_pid_interface_usage_and_product`，构造分别只错一个字段的枚举项，断言全部不打开且抛出 `DeviceNotFoundError`。

- [ ] **Step 2: 运行测试确认失败**

  Run: `python -m pytest tests/test_device.py::test_connect_ignores_wrong_pid_interface_usage_and_product -v`
  Expected: FAIL，原因是 `device` 模块尚不存在。

- [ ] **Step 3: 实现后端协议、身份常量和单候选连接**

  `HidBackend.enumerate(vid, pid) -> list[dict]`，`HidBackend.open_path(path: bytes) -> HidHandle`；匹配必须同时检查六个身份字段。实现 `FakeHidBackend` 和 `FakeHidHandle`，记录打开路径、写包及按队列返回的读包。

- [ ] **Step 4: 写查询队列测试并实现有界读写**

  添加 `test_query_drains_ack_and_stale_packets_then_collects_current_e3_pair`、`test_query_times_out_without_complete_pair`、`test_drain_stops_at_max_reads`。`query_state` 每次主动写入 `build_query_packet()`，在单调时钟截止前读取；非目标帧忽略，格式错误帧计入诊断并最终以 `DeviceProtocolError` 报告。

- [ ] **Step 5: 写多候选协议探测歧义测试并实现选择**

  添加 `test_connect_rejects_two_identity_matches_that_both_probe_successfully` 和 `test_connect_selects_only_candidate_with_valid_protocol_state`。连接流程先逐个短会话探测；零个有效设备抛 `DeviceNotFoundError`，两个以上有效设备抛 `AmbiguousDeviceError` 并关闭所有句柄。

- [ ] **Step 6: 写开关写入边界测试**

  添加 `test_write_power_accepts_only_power_target_enum_and_writes_exactly_64_bytes`，断言没有公开任意命令/载荷写入方法。

- [ ] **Step 7: 运行设备测试**

  Run: `python -m pytest tests/test_device.py -v`
  Expected: 全部 PASS，且测试结束后所有假句柄均关闭。

- [ ] **Step 8: 提交**

  ```bash
  git add src/halo_soundbar_service/device.py tests/fakes.py tests/test_device.py
  git commit -m "feat: add strict Halo HID device session"
  ```

### Task 4: 可验证、可取消的双灯开关事务

**Files:**
- Create: `src/halo_soundbar_service/controller.py`
- Create: `tests/test_controller.py`

**Interfaces:**
- Consumes: Task 3 的 `DeviceClient` 工厂、Task 1/2 的 `PowerTarget`、`AmbientState`、`style_fingerprint`。
- Produces: `OperationStatus(Enum)`；`OperationResult(status: OperationStatus, attempts: int, before: AmbientState | None, after: AmbientState | None, detail: str)`；`LightController(client_factory: Callable[[], DeviceClient], monotonic: Callable[[], float], sleep: Callable[[float], None])`；`set_power(enabled: bool, deadline: float, cancel_event: threading.Event | None = None) -> OperationResult`；`probe() -> AmbientState`。

- [ ] **Step 1: 写幂等与精确写入失败测试**

  添加 `test_set_power_writes_only_switches_not_already_at_target_in_main_then_sub_order` 和 `test_set_power_is_noop_when_both_switches_match`，断言调用序列仅包含 `query_state`、必要的 `write_power` 和验证查询。

- [ ] **Step 2: 运行测试确认失败**

  Run: `python -m pytest tests/test_controller.py -k "writes_only or noop" -v`
  Expected: FAIL，原因是 `LightController` 尚不存在。

- [ ] **Step 3: 实现快照校验和单轮事务**

  操作前必须确认 `0x0B`/`0x0C` 条目的固定字段与开关值合法；写入顺序固定 MAIN 后 SUBWOOFER；验证以查询到的两个目标值为准，不以 ACK 为准。

- [ ] **Step 4: 写三轮重试、截止时间和取消测试并实现循环**

  添加 `test_retries_only_remaining_switches_up_to_three_attempts`、`test_deadline_stops_retry_without_extra_write`、`test_cancel_before_write_returns_cancelled`。每轮先重新查询，仅补发尚未达到目标的开关；最多三轮且不得越过传入 deadline。

- [ ] **Step 5: 写样式保护和 TempoHub 并发测试**

  添加 `test_success_requires_style_fingerprint_unchanged` 和 `test_external_style_change_is_reported_without_restore_write`。若写入后样式变化，返回 `EXTERNAL_STATE_CHANGED` 并记录差异；断言写入历史仍只有两个开关命令，不发送任何恢复样式命令。

- [ ] **Step 6: 运行控制器测试**

  Run: `python -m pytest tests/test_controller.py -v`
  Expected: 全部 PASS。

- [ ] **Step 7: 提交**

  ```bash
  git add src/halo_soundbar_service/controller.py tests/test_controller.py
  git commit -m "feat: add verified light power transactions"
  ```

### Task 5: 服务生命周期、启动重试和 PRESHUTDOWN 优先级

**Files:**
- Create: `src/halo_soundbar_service/lifecycle.py`
- Create: `tests/test_lifecycle.py`

**Interfaces:**
- Consumes: Task 4 的 `LightController.set_power`、`OperationResult`。
- Produces: `StartupRetryPolicy(initial_delay_s=1.0, max_delay_s=5.0, total_timeout_s=120.0)`；`ServiceLifecycle(controller_factory, stop_event, monotonic, wait)`；`run_startup() -> OperationResult`；`request_stop() -> None`；`handle_preshutdown(budget_s: float = 12.0) -> OperationResult`；`handle_shutdown(budget_s: float = 12.0) -> OperationResult`。

- [ ] **Step 1: 写启动重试失败测试**

  添加 `test_startup_retries_device_not_ready_with_capped_backoff_until_success`，断言等待序列从 1 秒开始、单次不超过 5 秒、总截止不超过 120 秒；添加 `test_startup_gives_up_at_total_timeout_but_lifecycle_remains_alive`。

- [ ] **Step 2: 运行测试确认失败**

  Run: `python -m pytest tests/test_lifecycle.py -k startup -v`
  Expected: FAIL，原因是 `ServiceLifecycle` 尚不存在。

- [ ] **Step 3: 实现可取消启动流程**

  启动调用 `set_power(True, deadline, cancel_event)`；只有设备未就绪/暂时读取失败可重试，身份或协议不匹配立即失败。等待通过注入的可中断 `wait(seconds) -> bool` 完成。

- [ ] **Step 4: 写 PRESHUTDOWN 竞争、幂等和 STOP 测试**

  添加 `test_preshutdown_cancels_startup_then_runs_one_off_transaction_without_deadlock`、`test_duplicate_preshutdown_and_shutdown_share_result`、`test_manual_stop_never_calls_set_power`。用线程屏障让启动事务停在重试点，断言 PRESHUTDOWN 在释放后优先执行关灯且只执行一次。

- [ ] **Step 5: 实现单锁优先级与 12 秒关灯预算**

  第一次 PRESHUTDOWN 设置不可逆 `shutdown_requested`、触发取消事件并取得事务锁；后续 PRESHUTDOWN/SHUTDOWN 等待并复用第一次结果。STOP 只设置停止和取消事件。

- [ ] **Step 6: 运行生命周期测试**

  Run: `python -m pytest tests/test_lifecycle.py -v`
  Expected: 全部 PASS，测试进程没有遗留线程。

- [ ] **Step 7: 提交**

  ```bash
  git add src/halo_soundbar_service/lifecycle.py tests/test_lifecycle.py
  git commit -m "feat: coordinate startup and preshutdown lighting"
  ```

### Task 6: Windows Service 适配与双通道日志

**Files:**
- Create: `src/halo_soundbar_service/logging_setup.py`
- Create: `src/halo_soundbar_service/service.py`
- Create: `tests/test_service.py`

**Interfaces:**
- Consumes: Task 5 的 `ServiceLifecycle` 和 Task 4/3 的生产工厂。
- Produces: `configure_logging(program_data: Path | None = None) -> logging.Logger`；`HaloSoundbarLightService(win32serviceutil.ServiceFramework)`；`build_controller() -> LightController`；服务常量 `SERVICE_NAME="HaloSoundbarLightService"`、`PRESHUTDOWN_CONTROL=15`。

- [ ] **Step 1: 写接受控制码与路由失败测试**

  用 monkeypatch 的 pywin32 API 添加 `test_get_accepted_controls_includes_service_accept_preshutdown`、`test_svc_other_ex_routes_control_15_to_preshutdown`、`test_svc_shutdown_uses_same_off_path`、`test_svc_stop_only_requests_stop`。

- [ ] **Step 2: 运行测试确认失败**

  Run: `python -m pytest tests/test_service.py -v`
  Expected: FAIL，原因是 `service` 模块尚不存在。

- [ ] **Step 3: 实现薄 ServiceFramework 适配层**

  覆盖 `GetAcceptedControls()` 并 OR `win32service.SERVICE_ACCEPT_PRESHUTDOWN`；`SvcOtherEx(15, ...)` 先报告 `SERVICE_STOP_PENDING`、`waitHint=15000`，再调用生命周期关灯；`SvcShutdown` 复用该路径；`SvcStop` 不调用灯光控制。

- [ ] **Step 4: 写日志回退和轮转测试并实现**

  添加 `test_logging_uses_programdata_utf8_rotating_file_1mib_three_backups` 和 `test_file_log_failure_keeps_event_log_available`。关键生命周期结果同时送 Windows Application Event Log；文件路径为 `%ProgramData%\HaloSoundbarLightService\service.log`。

- [ ] **Step 5: 运行服务适配测试和此前测试**

  Run: `python -m pytest tests/test_service.py tests/test_lifecycle.py -v`
  Expected: 全部 PASS。

- [ ] **Step 6: 提交**

  ```bash
  git add src/halo_soundbar_service/logging_setup.py src/halo_soundbar_service/service.py tests/test_service.py
  git commit -m "feat: add Windows preshutdown service adapter"
  ```

### Task 7: CLI、服务注册和冻结入口

**Files:**
- Create: `src/halo_soundbar_service/installer.py`
- Create: `src/halo_soundbar_service/cli.py`
- Create: `src/halo_soundbar_service/__main__.py`
- Create: `tests/test_cli.py`
- Create: `tests/test_installer.py`

**Interfaces:**
- Consumes: Task 3/4 的 `probe`/`set_power`、Task 6 的服务类与名称。
- Produces: `configure_installed_service(api, executable: Path) -> None`；`install_service(executable: Path) -> None`；`uninstall_service() -> None`；`main(argv: Sequence[str] | None = None) -> int`；`dispatch(argv: Sequence[str], frozen: bool) -> int`。

- [ ] **Step 1: 写 CLI 返回码与只读 probe 失败测试**

  添加 `test_probe_prints_identity_effect_and_two_switch_states_without_writes`、`test_on_off_return_zero_only_after_verified_success`、`test_protocol_or_device_failure_returns_nonzero`，以注入控制器避免接触真实 USB。

- [ ] **Step 2: 运行测试确认失败**

  Run: `python -m pytest tests/test_cli.py -v`
  Expected: FAIL，原因是 `cli` 模块尚不存在。

- [ ] **Step 3: 实现 `probe/on/off/debug` 命令分派**

  `probe` 输出目标身份、灯组、效果编号及 MAIN/SUBWOOFER 状态；`on/off` 使用 12 秒 deadline；所有异常转成稳定非零返回码并写日志，默认不输出原始完整 HID 包。

- [ ] **Step 4: 写服务安装配置失败测试**

  添加 `test_install_registers_localsystem_automatic_service_and_configures_15000ms_preshutdown`、`test_install_sets_description_and_restart_failure_actions`、`test_uninstall_stops_then_removes_service_but_keeps_logs`。测试只断言传给抽象 SCM API 的参数，不实际修改服务。

- [ ] **Step 5: 实现安装器**

  使用 pywin32 注册当前冻结 EXE，账户为 LocalSystem、启动类型 Automatic；调用 `ChangeServiceConfig2` 设置描述、失败重启和 `SERVICE_CONFIG_PRESHUTDOWN_INFO=15000`。卸载仅停止并删除服务注册，不删除 ProgramData 日志。

- [ ] **Step 6: 写冻结入口分派测试并实现入口**

  添加 `test_frozen_no_args_enters_service_dispatcher`、`test_any_user_command_enters_cli`、`test_source_module_no_args_shows_help_instead_of_dispatcher`。冻结 EXE 无参数时调用 `servicemanager.StartServiceCtrlDispatcher()`；存在命令参数时调用 CLI。

- [ ] **Step 7: 运行 CLI/安装器/服务测试**

  Run: `python -m pytest tests/test_cli.py tests/test_installer.py tests/test_service.py -v`
  Expected: 全部 PASS，且没有创建真实服务。

- [ ] **Step 8: 提交**

  ```bash
  git add src/halo_soundbar_service/installer.py src/halo_soundbar_service/cli.py src/halo_soundbar_service/__main__.py tests/test_cli.py tests/test_installer.py
  git commit -m "feat: add service CLI and installation workflow"
  ```

### Task 8: 可复现打包、管理员脚本与操作文档

**Files:**
- Create: `HaloSoundbarLightService.spec`
- Create: `scripts/build.ps1`
- Create: `scripts/install.ps1`
- Create: `scripts/uninstall.ps1`
- Create: `README.md`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: Task 7 的 `python -m halo_soundbar_service` 入口和 install/uninstall 命令。
- Produces: `dist/HaloSoundbarLightService.exe`、`dist/HaloSoundbarLightService.exe.sha256`；面向管理员的构建、安装、诊断和回滚流程。

- [ ] **Step 1: 写 PyInstaller spec 与静态验证测试**

  在 `tests/test_installer.py` 添加 `test_pyinstaller_spec_contains_win32timezone_hid_and_service_entrypoint`，确认隐藏导入至少包含 `win32timezone` 和 `hid`，产物名固定为 `HaloSoundbarLightService`。

- [ ] **Step 2: 运行静态测试确认失败**

  Run: `python -m pytest tests/test_installer.py::test_pyinstaller_spec_contains_win32timezone_hid_and_service_entrypoint -v`
  Expected: FAIL，原因是 spec 文件尚不存在。

- [ ] **Step 3: 实现构建配置与脚本**

  `scripts/build.ps1` 使用当前 Python 创建 `.venv`、安装 `requirements-build.txt`、运行全套 pytest、清理项目内 `build/` 和 `dist/` 后执行 PyInstaller，最后生成 SHA-256 文件；任何一步失败立即退出非零。

- [ ] **Step 4: 实现安装和卸载包装脚本**

  两个脚本先检查管理员权限；安装脚本复制 EXE 到 `%ProgramFiles%\HaloSoundbarLightService` 后调用 `install` 并启动服务；卸载脚本调用 `uninstall`，仅在服务成功移除后删除 Program Files 下该服务目录，不删除源码与 ProgramData 日志。

- [ ] **Step 5: 编写 README**

  覆盖支持的精确设备身份、四条用户命令、安装前 `probe/off/on` 顺序、服务行为、日志路径、TempoHub 共存、非零错误码、卸载回滚，以及“真实关机测试必须人工确认并保存工作”的醒目说明。

- [ ] **Step 6: 运行完整自动化测试**

  Run: `python -m pytest -v`
  Expected: 全部 PASS，无跳过测试；测试不得访问真实 HID 或 SCM。

- [ ] **Step 7: 提交**

  ```bash
  git add HaloSoundbarLightService.spec scripts README.md pyproject.toml tests/test_installer.py
  git commit -m "build: package Halo light service executable"
  ```

### Task 9: 打包产物与真实设备的非破坏性集成验证

**Files:**
- Modify: `README.md`（记录验证命令和已知结果，不写机器专属路径）
- Create: `docs/verification/2026-10-18-local-verification.md`

**Interfaces:**
- Consumes: Task 8 的 EXE、SHA-256 和 README 流程。
- Produces: 自动化测试、打包烟雾测试、真实设备开关往返和样式不变性证据。

- [ ] **Step 1: 执行清洁构建**

  Run: `powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1`
  Expected: pytest 全部 PASS，生成 EXE 和 SHA-256，PyInstaller 无缺失模块错误。

- [ ] **Step 2: 验证打包入口和只读探测**

  Run: `.\dist\HaloSoundbarLightService.exe probe`
  Expected: 唯一匹配 PID `A073`/接口 `4`/Usage Page `FF14`，显示两个开关和效果编号，退出码 0。

- [ ] **Step 3: 执行可回滚开关往返**

  先保存 `probe` 快照；运行 EXE `off` 并再次 `probe`，确认两开关为 0；随后运行 EXE `on` 并再次 `probe`，确认两开关为 1。比较三次状态，断言效果编号和索引 `0x01..0x0A` 的数据不变。若任一步失败，立即停止后续写入并保留日志。

- [ ] **Step 4: 记录验证证据**

  在 `docs/verification/2026-10-18-local-verification.md` 记录 Python/依赖版本、测试数量、构建摘要、EXE SHA-256、设备身份、开关前后状态和样式指纹；不记录完整设备路径或用户隐私数据。

- [ ] **Step 5: 提交验证记录**

  ```bash
  git add README.md docs/verification/2026-10-18-local-verification.md
  git commit -m "test: verify packaged service with Halo SoundBar Max"
  ```

### Task 10: 服务安装与等效 PRESHUTDOWN 验证

**Files:**
- Modify: `docs/verification/2026-10-18-local-verification.md`

**Interfaces:**
- Consumes: Task 9 已验证的 EXE 和 Task 7 的安装器。
- Produces: 已注册服务配置证据、启动开灯证据和不关机的 PRESHUTDOWN 控制路径证据。

- [ ] **Step 1: 在执行前请求管理员操作批准**

  明确说明将复制 EXE 到 Program Files、创建 Automatic/LocalSystem 服务并启动服务；获得批准后才执行安装脚本。

- [ ] **Step 2: 安装并检查服务配置**

  Run: `powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1`
  Expected: 服务状态 RUNNING；启动类型 Automatic；账户 LocalSystem；PreshutdownTimeout 为 15000；接受控制包含 PRESHUTDOWN。

- [ ] **Step 3: 验证服务启动开灯**

  在先用 CLI 关灯后重启服务，等待启动事务完成并运行 `probe`；Expected: 两组开关均为 1，样式指纹不变。注意手动停止本身不得改变灯光。

- [ ] **Step 4: 用受控服务控制调用验证 PRESHUTDOWN 路由**

  在不关闭电脑的测试路径中向服务处理层注入控制码 15，或使用专用测试入口调用相同生命周期方法；Expected: 两组开关均为 0、服务在预算内结束、事件日志包含成功记录。不得使用会实际发起系统关机的命令。

- [ ] **Step 5: 恢复日常状态并记录证据**

  启动服务或运行 `on` 恢复两组灯为开启，在验证文档记录 SCM 配置、事件日志摘要、耗时、最终灯光状态和样式指纹。

- [ ] **Step 6: 提交**

  ```bash
  git add docs/verification/2026-10-18-local-verification.md
  git commit -m "test: verify installed service lifecycle"
  ```

### Task 11: 真实关机/开机验收（必须单独授权）

**Files:**
- Modify: `docs/verification/2026-10-18-local-verification.md`

**Interfaces:**
- Consumes: Task 10 已安装且已通过等效验证的服务。
- Produces: 真实 Windows PRESHUTDOWN 关灯与下次启动开灯的最终验收证据。

- [ ] **Step 1: 单独请求真实关机授权**

  向用户说明电脑将真实关机，要求其确认所有工作已保存并明确同意当前执行；没有该次明确授权则停止在 Task 10，不把此项标记完成。

- [ ] **Step 2: 执行真实关机并由用户观察关灯**

  仅在授权后使用正常 Windows 关机流程。Expected: Windows 完成关机前主机与低音箱氛围灯均关闭，关机不被服务长时间阻塞。

- [ ] **Step 3: 开机后核验**

  用户重新开机后，检查服务事件日志与文件日志，运行 `probe`。Expected: 日志显示 PRESHUTDOWN 在 12 秒预算内完成；自动启动事务将两组灯恢复为开启；样式指纹保持不变。

- [ ] **Step 4: 记录最终验收或明确保留项并提交**

  若用户选择暂不执行真实关机，在验证文档明确记为“经用户接受的未执行验收项”，不能声称已完成真实关机验证。

  ```bash
  git add docs/verification/2026-10-18-local-verification.md
  git commit -m "test: record real shutdown startup acceptance"
  ```

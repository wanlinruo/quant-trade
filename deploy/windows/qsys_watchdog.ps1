# 文件编码：UTF-8 with BOM + CRLF。Windows PowerShell 5.1 没有 BOM 时按 GBK 读，
# 中文注释会被读乱并导致解析失败（2026-09-18 实测）。改动后请保持这两项。
# qsys Windows 看门狗：计划任务只在登录时触发，进程崩了不会自己起来。
#
# 2026-09-18：影子执行代理 05:06:47 崩溃（0xC0000409），无人登录，任务一直是
# Ready，影子核心因此 5.5 小时取不到券商账户快照，当日 09:30 竞价事件过期未跑。
#
# 2026-10-05：同一个代理换了一种死法——miniQMT 08:45:04 自行重启，代理的 xtquant
# 同步调用永久阻塞（xtquant 内部 Future.result() 不带 timeout，回调永不到达），
# 进程活着 11 小时 45 分，心跳冻结在那一秒，日志一行没写。本脚本当时全程报告
# 正常并什么都没做——因为它只问「进程在不在」，而进程在。判据选错时它不报警，
# 它发出「一切正常」。
#
# 所以现在有两道判据：
#   1. 进程不在         -> 触发计划任务（崩溃型，含代理自己 os._exit 的停滞退出）
#   2. 进程在但心跳过期 -> 先杀掉再触发（卡死型，进程存在判据看不见的那种）
# 心跳只在主循环走完一轮后才写，所以它是「还在干活」的证据，进程存在不是。
#
# 每 5 分钟执行一次；不判断行情时段——盘后同样要在线，否则收盘对账与次日盘前
# 都会缺人。Redis 读不到不等于代理死了，那种情况只记日志、不重启：重启一个好
# 进程比留着一个死进程更坏。

$ErrorActionPreference = 'Stop'
$log = 'C:\qsys\live_app\var\watchdog.log'

$python = 'C:\qsys\.venv\Scripts\python.exe'
$appDir = 'C:\qsys\live_app'
# live_app 是源码快照，不是安装进 venv 的包；代理的 cmd 会设这项，SYSTEM
# 身份运行的看门狗不会继承它，必须在这里显式设置。
$env:PYTHONPATH = Join-Path $appDir 'src'

# Heartbeat = 心跳里的 source 名；为空表示该目标只做进程存在检查。
# 阈值 120s：主循环 1s 一轮，代理自己的停滞自检阈值是 60s，留一倍余量，
# 避免在代理正准备自己退出时抢着动手。
$targets = @(
    @{ Task = 'qsys-agent-shadow';   Match = 'run_agent.py';          Heartbeat = 'agent'; MaxAge = 120 },
    @{ Task = 'qsys-market-gateway'; Match = 'run_market_gateway.py'; Heartbeat = '';      MaxAge = 0 }
)

function Write-Line($text) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $text
    Add-Content -Path $log -Value $line -Encoding UTF8
}

function Start-Target($target, $why) {
    Write-Line ('{0} {1}，触发计划任务' -f $target.Match, $why)
    try {
        schtasks /run /tn $target.Task | Out-Null
        Write-Line ('{0} 已触发' -f $target.Task)
    } catch {
        Write-Line ('{0} 触发失败：{1}' -f $target.Task, $_.Exception.Message)
    }
}

foreach ($target in $targets) {
    $running = Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -like ('*' + $target.Match + '*') }

    if (-not $running) {
        Start-Target $target '未在运行'
        continue
    }

    if (-not $target.Heartbeat) { continue }

    # 进程在。再问它是否还在干活。
    Push-Location $appDir
    try {
        $detail = & $python 'scripts\check_agent_alive.py' `
            '--config' 'config\deploy.local.toml' `
            '--source' $target.Heartbeat `
            '--max-age' $target.MaxAge 2>&1 | Out-String
        $code = $LASTEXITCODE
    } catch {
        Write-Line ('{0} 心跳检查无法执行：{1}' -f $target.Heartbeat, $_.Exception.Message)
        Pop-Location
        continue
    }
    Pop-Location
    $detail = $detail.Trim()

    if ($code -eq 0) { continue }

    if ($code -eq 4) {
        # Redis 不可达 ≠ 代理死了。不动它。
        Write-Line ('{0} 心跳不可读，本轮不处理：{1}' -f $target.Heartbeat, $detail)
        continue
    }

    # 进程活着但心跳过期：先杀掉。卡在 xtquant Future.result() 的线程收不回来，
    # 不杀就会和新起的实例抢同一把单实例锁，新实例起不来。
    Write-Line ('{0} 进程在但心跳异常（退出码 {1}）：{2}' -f $target.Heartbeat, $code, $detail)
    foreach ($proc in $running) {
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
            Write-Line ('已杀掉卡死进程 pid={0}' -f $proc.ProcessId)
        } catch {
            Write-Line ('杀进程 pid={0} 失败：{1}' -f $proc.ProcessId, $_.Exception.Message)
        }
    }
    Start-Sleep -Seconds 3
    Start-Target $target '卡死已清除'
}

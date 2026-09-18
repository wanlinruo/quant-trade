# 文件编码：UTF-8 with BOM + CRLF。Windows PowerShell 5.1 没有 BOM 时按 GBK 读，
# 中文注释会被读乱并导致解析失败（2026-09-18 实测）。改动后请保持这两项。
# qsys Windows 看门狗：计划任务只在登录时触发，进程崩了不会自己起来。
#
# 2026-09-18：影子执行代理 05:06:47 崩溃（0xC0000409），无人登录，任务一直是
# Ready，影子核心因此 5.5 小时取不到券商账户快照，当日 09:30 竞价事件过期未跑。
#
# 本脚本由 qsys-watchdog 计划任务每 5 分钟执行一次：按命令行特征判断进程在不在，
# 不在就触发对应的计划任务。只启动，不杀进程；不判断行情时段——盘后同样要在线，
# 否则收盘对账与次日盘前都会缺人。

$ErrorActionPreference = 'Stop'
$log = 'C:\qsys\live_app\var\watchdog.log'

$targets = @(
    @{ Task = 'qsys-agent-shadow';   Match = 'run_agent.py' },
    @{ Task = 'qsys-market-gateway'; Match = 'run_market_gateway.py' }
)

function Write-Line($text) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $text
    Add-Content -Path $log -Value $line -Encoding UTF8
}

foreach ($target in $targets) {
    $running = Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -like ('*' + $target.Match + '*') }
    if ($running) { continue }
    Write-Line ('{0} 未在运行，触发计划任务' -f $target.Match)
    try {
        schtasks /run /tn $target.Task | Out-Null
        Write-Line ('{0} 已触发' -f $target.Task)
    } catch {
        Write-Line ('{0} 触发失败：{1}' -f $target.Task, $_.Exception.Message)
    }
}

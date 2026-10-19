# 安装 git 钩子：提交前自动拦截 API Key
#
# 为什么用 core.hooksPath 而不是往 .git/hooks 里拷文件：
#   .git/hooks 不纳入版本控制，换一台机器 / 重新 clone 就没了，等于没装。
#   指向仓库里的 scripts/hooks 之后，钩子跟着代码走，一次安装长期有效。
#
# 用法（在仓库根目录）：
#   pwsh scripts/install_hooks.ps1
# 卸载：
#   git config --unset core.hooksPath

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".git")) {
    Write-Host "请在仓库根目录运行（当前没有 .git）" -ForegroundColor Red
    exit 1
}

git config core.hooksPath scripts/hooks
Write-Host "已设置 core.hooksPath = scripts/hooks" -ForegroundColor Green
Write-Host ""
Write-Host "验证一下（应该输出 OK）：" -ForegroundColor Cyan
$python = ".\.venv\Scripts\python.exe"
if (Test-Path $python) {
    & $python backend/tools/check_secrets.py
} else {
    python backend/tools/check_secrets.py
}

Write-Host ""
Write-Host "现在每次 git commit 都会先扫描暂存区；发现疑似密钥会直接阻止提交。" -ForegroundColor Green
Write-Host "想跳过检查（真的知道自己在干什么时）：git commit --no-verify" -ForegroundColor Yellow

param()
Write-Host "► Preverjam repozitorij"
if (Test-Path ".git") {
    Write-Host "► git pull origin main"
    git pull origin main
} else {
    Write-Host "► .git mape ni, preskakujem pull"
}

Write-Host "► Namestitev click, če manjka"
if (-not (pip show click -ErrorAction SilentlyContinue)) {
    pip install click
}

Write-Host "► Namestitev odvisnosti"
pip install --upgrade pip
pip install -r requirements.txt
if (Test-Path "wsm_program_vnasanje\requirements.txt") {
    Write-Host "► Namestitev nested odvisnosti"
    pip install -r wsm_program_vnasanje\requirements.txt
}

Write-Host "► Zagon validacije"
python -m wsm validate tests

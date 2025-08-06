<#
  Fix-Run.ps1  –  posodobi repo, poskrbi za .venv + odvisnosti
                 in zažene program  (privzeta veja = main, spremeni z -Branch)
#>

param (
    [string]$Branch = 'main'
)

$ErrorActionPreference = 'Stop'      # ustavi ob prvi napaki
Set-Location $PSScriptRoot           # mapo, kjer je skripta

# ───────────────────────────────────── Git ─────────────────────────────────────
Write-Host "[git] fetch origin" -f Cyan
git fetch origin

Write-Host "[git] checkout $Branch" -f Cyan
git checkout $Branch

Write-Host "[git] pull origin $Branch" -f Cyan
git pull origin $Branch


# ───────────────────────── virtualno okolje (.venv) ───────────────────────────
$venvPath   = Join-Path $PSScriptRoot ".venv"
$activatePS = Join-Path $venvPath "Scripts\Activate.ps1"

if (-not (Test-Path $activatePS)) {
    Write-Host "[env] create .venv" -f Cyan
    python -m venv .venv
}

Write-Host "[env] activate .venv" -f Cyan
& $activatePS


# ───────────────────────────── odvisnosti (pip) ───────────────────────────────
Write-Host "[pip] upgrade pip" -f Cyan
python -m pip install --upgrade pip

if (Test-Path 'requirements.txt') {
    Write-Host "[pip] install -r requirements.txt" -f Cyan
    python -m pip install --upgrade -r requirements.txt
}

# -- defusedxml je kritičen za varno XML --
Write-Host "[pip] ensure defusedxml" -f Cyan
python -m pip install --upgrade defusedxml


# ───────────────────── poti do NAS (WSM_* environment) ────────────────────────
Write-Host "[env] set WSM_* variables" -f Cyan
$env:WSM_LINKS_DIR      = '\\PisarnaNAS\wsm_program_vnasanje_povezave\links'
$env:WSM_KEYWORDS_FILE  = '\\PisarnaNAS\wsm_program_vnasanje_povezave\kljucne_besede_wsm_kode.xlsx'
$env:WSM_CODES_FILE     = '\\PisarnaNAS\wsm_program_vnasanje_povezave\sifre_wsm.xlsx'


# ─────────────────────────────── zagon programa ───────────────────────────────
Write-Host "[run] python -m wsm.run" -f Cyan
python -m wsm.run
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    Write-Error "wsm.run exited with code $exitCode"
    exit $exitCode
}

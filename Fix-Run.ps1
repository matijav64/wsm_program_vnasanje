<#
  Fix-Run.ps1   –   posodobi repo, aktivira .venv, nastavi pot do NAS-povezav
                    in zažene program   (privzeta veja = main, spremeni z -Branch)
#>

param (
    [string]$Branch = 'main'
)

$ErrorActionPreference = 'Stop'      # takoj ustavi ob prvi napaki
Set-Location $PSScriptRoot           # v mapo, kjer leži skripta

# ---------- Git ----------------------------------------------------------------
Write-Host "[git] fetch origin" -f Cyan
git fetch origin

Write-Host "[git] checkout $Branch" -f Cyan
git checkout $Branch

Write-Host "[git] pull origin $Branch" -f Cyan
git pull origin $Branch


# ---------- virtualno okolje ---------------------------------------------------
Write-Host "[env] activate .venv" -f Cyan
$activate = "$PSScriptRoot\.venv\Scripts\Activate.ps1"
if (-not (Test-Path $activate)) {
    Write-Error "Virtual environment not found: $activate`nUstvari ga z  python -m venv .venv"
    exit 1
}
& $activate


# ---------- odvisnosti ---------------------------------------------------------
if (Test-Path 'requirements.txt') {
    Write-Host "[pip] install -r requirements.txt" -f Cyan
    python -m pip install --upgrade --quiet -r requirements.txt
}


# ---------- poti na NAS --------------------------------------------------------
Write-Host "[env] set WSM_* variables" -f Cyan
$env:WSM_LINKS_DIR      = '\\PisarnaNAS\wsm_program_vnasanje_povezave\links'
$env:WSM_KEYWORDS_FILE  = '\\PisarnaNAS\wsm_program_vnasanje_povezave\kljucne_besede_wsm_kode.xlsx'
$env:WSM_CODES_FILE     = '\\PisarnaNAS\wsm_program_vnasanje_povezave\sifre_wsm.xlsx'


# ---------- zagon --------------------------------------------------------------
Write-Host "[run] python -m wsm.run" -f Cyan
python -m wsm.run

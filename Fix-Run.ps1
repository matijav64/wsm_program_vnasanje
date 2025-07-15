# Update repository and run WSM application

# Change to directory where script resides
Set-Location $PSScriptRoot

# Pull latest changes from Git
git pull

# Activate virtual environment
$activate = Join-Path '.venv' 'Scripts' 'Activate.ps1'
if (Test-Path $activate) {
    & $activate
} else {
    Write-Error "Virtual environment not found: $activate"
    exit 1
}

# Set required environment variables
$env:WSM_LINKS_DIR = Join-Path $PSScriptRoot 'links'
$env:WSM_KEYWORDS_FILE = Join-Path $PSScriptRoot 'kljucne_besede_wsm_kode.xlsx'
$env:WSM_CODES_FILE = Join-Path $PSScriptRoot 'sifre_wsm.xlsx'

# Install dependencies
if (Test-Path 'requirements.txt') {
    python -m pip install -r requirements.txt
}

# Launch application
python -m wsm.run

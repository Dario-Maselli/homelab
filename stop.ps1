# Remove the networks
# Remove 'shared-db-net' network if it exists
$net = docker network ls --format '{{.Name}}' | Where-Object { $_ -eq 'shared-db-net' }
if ($net) {
    Write-Host "Removing Docker network: shared-db-net"
    docker network rm shared-db-net
} else {
    Write-Host "Network 'shared-db-net' does not exist, nothing to remove."
}

# run-all-compose.ps1
$folders = @(
    "core",
    "storage",
    "all_the_rrs",
    "automation",
    "databases",
    "media",
    "monitoring"
)

foreach ($folder in $folders) {
    Write-Host "Running docker compose in $folder..."
    Push-Location $folder
    if (Test-Path "docker-compose.yml") {
        docker compose down -d
    } elseif (Test-Path "docker-compose.yaml") {
        docker compose -f docker-compose.yaml down -d
    }
    Pop-Location
}

# Run with:
# .\stop.ps1
# OR
# powershell -ExecutionPolicy Bypass -File .\stop.ps1

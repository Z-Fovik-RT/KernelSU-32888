[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9_.-]+$')]
    [string]$RepositoryName = 'KernelSU-32888'
)

$ErrorActionPreference = 'Stop'
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))

function Run-Checked {
    param([string]$Program, [string[]]$CommandArgs)
    & $Program @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "$Program failed with exit code $LASTEXITCODE"
    }
}

Push-Location -LiteralPath $repoRoot
try {
    Run-Checked gh @('auth', 'status', '--hostname', 'github.com')
    $account = (& gh api user --jq .login).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $account) {
        throw 'GitHub login is required. Run: gh auth login --hostname github.com --git-protocol https --scopes workflow --web'
    }
    $repository = "$account/$RepositoryName"
    $branch = 'zfovik-32888'
    $commit = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read source commit' }
    $dirty = & git status --porcelain --untracked-files=normal
    if ($dirty) { throw 'Commit the custom source changes before starting the build' }

    $existing = & gh api "repos/$repository" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Run-Checked gh @('repo', 'fork', 'tiann/KernelSU', '--fork-name', $RepositoryName, '--clone=false', '--remote=false')
        $existing = & gh api "repos/$repository"
        if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the created fork' }
    }
    $details = ($existing -join "`n") | ConvertFrom-Json
    if (-not $details.fork -or $details.source.full_name -ne 'tiann/KernelSU') {
        throw "$repository is not a KernelSU fork; refusing to change an unrelated repository"
    }

    $remoteUrl = "https://github.com/$repository.git"
    $forkUrl = & git remote get-url custom 2>$null
    if ($LASTEXITCODE -eq 0) {
        if ($forkUrl -ne $remoteUrl) { throw 'The custom remote points to a different repository' }
    } else {
        Run-Checked git @('remote', 'add', 'custom', $remoteUrl)
    }
    Run-Checked git @('-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential', 'push', 'custom', "HEAD:refs/heads/$branch")
    Run-Checked gh @('repo', 'edit', $repository, '--default-branch', $branch)
    Run-Checked gh @('api', '--method', 'PUT', "repos/$repository/actions/permissions", '-F', 'enabled=true')
    Run-Checked gh @('workflow', 'enable', 'build-32888.yml', '--repo', $repository)
    Run-Checked gh @('workflow', 'run', 'build-32888.yml', '--repo', $repository, '--ref', $branch)

    $runId = $null
    for ($attempt = 0; $attempt -lt 12; $attempt++) {
        $runJson = & gh run list --repo $repository --workflow build-32888.yml --branch $branch --event workflow_dispatch --limit 5 --json databaseId,headSha,url
        if ($LASTEXITCODE -ne 0) { throw 'Cannot read workflow runs' }
        $run = @($runJson | ConvertFrom-Json) | Where-Object { $_.headSha -eq $commit } | Select-Object -First 1
        if ($run) { $runId = $run.databaseId; break }
        Start-Sleep -Seconds 3
    }
    if (-not $runId) { throw 'Workflow submitted; run ID not yet visible. Inspect the repository Actions page.' }
    Write-Host $run.url
    Run-Checked gh @('run', 'watch', "$runId", '--repo', $repository, '--exit-status', '--interval', '10')

    $artifactDir = Join-Path $repoRoot 'dist'
    if (Test-Path -LiteralPath $artifactDir) {
        if (Get-ChildItem -LiteralPath $artifactDir -Force | Select-Object -First 1) {
            $artifactDir = Join-Path $repoRoot "dist-$runId"
        }
    }
    Run-Checked gh @('run', 'download', "$runId", '--repo', $repository, '--name', 'ZFovikRT-KernelSU-32888-android17-6.18', '--dir', $artifactDir)
    $module = Join-Path $artifactDir 'kernelsu_32888_android17-6.18.ko'
    Run-Checked python @('scripts/verify_lkm_version.py', $module, '--expected-version', '32888', '--build-commit', $commit, '--source-baseline', '64833c005eca6a8dc3d73ec2d37d84c37e0d9ad2')
    Write-Host "Verified build output: $artifactDir"
} finally {
    Pop-Location
}

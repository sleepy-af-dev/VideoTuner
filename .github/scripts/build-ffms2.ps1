<#
.SYNOPSIS
    Build ffms2 from source and place ffms2.dll and ffmsindex.exe in -OutDir.

.DESCRIPTION
    ffms2 has published no binary since 5.0, so the bundled release builds one
    from a later commit instead. Everything is pinned: the ffms2 commit, the
    AviSynthPlus commit its headers come from, and the vcpkg tag that decides
    which FFmpeg gets linked. The FFmpeg version is read back from vcpkg after
    installing rather than assumed, since the pin is only worth something if it
    is checked.

    Intended for CI, where vcpkg's binary cache can be restored between runs and
    the FFmpeg compile only happens when a pin changes.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$OutDir,
    # Defaults under the runner temp directory; resolved in the body because a
    # param default cannot use an if expression
    [string]$WorkDir,
    # Upstream ffms2 CI builds with v143, and it is what the runner image has.
    # Overridable for a build environment offering a different toolset.
    [string]$Toolset = 'v143'
)

$ErrorActionPreference = 'Stop'

if (-not $WorkDir) {
    $tempRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
    $WorkDir = Join-Path $tempRoot 'ffms2-build'
}

# ----------------------------- Pinned configuration -----------------------------
# Full SHAs rather than abbreviations. An abbreviation is resolved against the
# repository as it stands, so it is a weaker pin than it looks, and this commit
# is what identifies the corresponding source for the GPL binary we ship.
$FFMS2_COMMIT = '3af2ef2ae47bc30b64597c9e419e5b19c4bda7d8'
# Only avs_core\include is used, so this is fetched one commit deep. A shallow
# fetch-by-commit will not resolve an abbreviated SHA in any case.
$AVS_COMMIT      = 'cfdaf8eb8a0a05b14edf7e73736df382bb876592'
$VCPKG_TAG       = '2026.07.29'
$FFMPEG_VERSION  = '8.1.2'
$TRIPLET         = 'x64-windows-static'
$FFMPEG_FEATURES = 'avcodec,avdevice,avfilter,avformat,swresample,swscale,zlib,bzip2,core,dav1d,gpl,version3,lzma,openssl,xml2'

$Ffms2Repo = 'https://github.com/FFMS/ffms2.git'
$AvsRepo   = 'https://github.com/AviSynth/AviSynthPlus.git'
$VcpkgRepo = 'https://github.com/microsoft/vcpkg.git'

function Info($m) { Write-Host "[ffms2] $m" }
function Die($m)  { Write-Host "[ffms2] ERROR: $m"; exit 1 }
function Run($file, [string[]]$argList) {
    & $file @argList
    if ($LASTEXITCODE -ne 0) { Die "command failed (exit $LASTEXITCODE): $file $($argList -join ' ')" }
}

# A shortened SHA still checks out, so nothing else would notice the pin being
# weakened. This is the only thing that catches it.
foreach ($pin in @{ ffms2 = $FFMS2_COMMIT; AviSynthPlus = $AVS_COMMIT }.GetEnumerator()) {
    if ($pin.Value -notmatch '^[0-9a-f]{40}$') {
        Die "$($pin.Key) commit pin must be a full 40-character SHA, got '$($pin.Value)'"
    }
}

# Resolve to absolute before deriving anything from them: git -C and MSBuild are
# run from varying working directories, and vcpkg rejects a relative binary cache
# with an error that points at the ffmpeg install rather than at the path
New-Item -ItemType Directory -Force $WorkDir, $OutDir | Out-Null
$WorkDir = (Resolve-Path $WorkDir).Path
$OutDir = (Resolve-Path $OutDir).Path
if ($env:VCPKG_DEFAULT_BINARY_CACHE) {
    New-Item -ItemType Directory -Force $env:VCPKG_DEFAULT_BINARY_CACHE | Out-Null
    $env:VCPKG_DEFAULT_BINARY_CACHE = (Resolve-Path $env:VCPKG_DEFAULT_BINARY_CACHE).Path
}

# ffms2.vcxproj hardcodes ../../AviSynthPlus/avs_core/include, so these two must
# be siblings whatever the work directory is
$Ffms2Dir = Join-Path $WorkDir 'ffms2'
$AvsDir   = Join-Path $WorkDir 'AviSynthPlus'
$VcpkgDir = Join-Path $WorkDir 'vcpkg'

# --------------------------------- 1. sources -----------------------------------
if (-not (Test-Path (Join-Path $Ffms2Dir '.git'))) {
    Info "cloning ffms2"
    Run 'git' @('clone', '--quiet', $Ffms2Repo, $Ffms2Dir)
}
Run 'git' @('-C', $Ffms2Dir, 'fetch', '--quiet', 'origin')
Run 'git' @('-C', $Ffms2Dir, 'checkout', '--quiet', '--detach', $FFMS2_COMMIT)

if (-not (Test-Path (Join-Path $AvsDir '.git'))) {
    Info "fetching AviSynthPlus headers at $($AVS_COMMIT.Substring(0,7))"
    New-Item -ItemType Directory -Force $AvsDir | Out-Null
    Run 'git' @('-C', $AvsDir, 'init', '--quiet')
    Run 'git' @('-C', $AvsDir, 'remote', 'add', 'origin', $AvsRepo)
    Run 'git' @('-C', $AvsDir, 'fetch', '--quiet', '--depth', '1', 'origin', $AVS_COMMIT)
    Run 'git' @('-C', $AvsDir, 'checkout', '--quiet', '--detach', 'FETCH_HEAD')
}
$avsHdr = Join-Path $AvsDir 'avs_core\include\avisynth.h'
if (-not (Test-Path $avsHdr)) { Die "AviSynthPlus checked out but $avsHdr is missing" }

# ------------------------------ 2. FFmpeg via vcpkg -----------------------------
if (-not (Test-Path (Join-Path $VcpkgDir '.git'))) {
    Info "cloning vcpkg at $VCPKG_TAG"
    Run 'git' @('clone', '--quiet', '--depth', '1', '--branch', $VCPKG_TAG, $VcpkgRepo, $VcpkgDir)
}
$Vcpkg = Join-Path $VcpkgDir 'vcpkg.exe'
if (-not (Test-Path $Vcpkg)) {
    Run (Join-Path $VcpkgDir 'bootstrap-vcpkg.bat') @('-disableMetrics')
}

Info "installing ffmpeg:$TRIPLET (restored from the binary cache when warm)"
Run $Vcpkg @("install", "ffmpeg[$FFMPEG_FEATURES]:$TRIPLET", '--clean-after-build')

# Read back what vcpkg actually installed rather than trusting the tag
$installed = (& $Vcpkg 'list' '--x-full-desc') -join "`n"
$m = [regex]::Match($installed, "(?m)^ffmpeg:$([regex]::Escape($TRIPLET))\s+(\S+)")
if (-not $m.Success) { Die "vcpkg reported success but ffmpeg:$TRIPLET is not installed" }
$installedVersion = $m.Groups[1].Value
if ($installedVersion -notlike "$FFMPEG_VERSION*") {
    Die ("FFmpeg pin not honoured: expected $FFMPEG_VERSION, vcpkg $VCPKG_TAG installed " +
         "'$installedVersion'. Update `$VCPKG_TAG and `$FFMPEG_VERSION together.")
}
Info "ffmpeg $installedVersion"

# ------------------------------- 3. build ffms2 ---------------------------------
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) { Die 'vswhere not found - is Visual Studio installed?' }
$MSBuild = @(& $vswhere -latest -products * -requires Microsoft.Component.MSBuild -find 'MSBuild\**\Bin\MSBuild.exe') |
           Select-Object -First 1
if (-not $MSBuild) { Die 'MSBuild not found' }

Info "building ffms2.sln (Release / x64 / $Toolset)"
Run $Vcpkg @('integrate', 'install')
# Leave the bare filename in the debug directory instead of an absolute build path
$env:LINK = '/PDBALTPATH:%_PDB%'
Run $MSBuild @((Join-Path $Ffms2Dir 'build-msvc\ffms2.sln'), '/t:Rebuild',
               '/p:Configuration=Release', '/p:Platform=x64',
               "/p:PlatformToolset=$Toolset", '/m', '/v:minimal', '/nologo')

# ------------------------------ 4. collect output -------------------------------
$rel = Join-Path $Ffms2Dir 'build-msvc\bin\x64\Release'
foreach ($name in 'ffms2.dll', 'ffmsindex.exe') {
    $built = Join-Path $rel $name
    if (-not (Test-Path $built)) { Die "MSBuild reported success but $built is missing" }
    Copy-Item $built $OutDir -Force
}

# The version string lives in the header; there is no release tag to read it from
$hdr = Get-Content (Join-Path $Ffms2Dir 'include\ffms.h') -Raw
$vm = [regex]::Match($hdr,
    '(?m)^\s*#define\s+FFMS_VERSION\s+\(\(\s*(\d+)\s*<<\s*24\)\s*\|\s*\(\s*(\d+)\s*<<\s*16\)\s*\|\s*\(\s*(\d+)\s*<<\s*8\)\s*\|\s*(\d+)\)')
$version = if ($vm.Success) {
    $v = '{0}.{1}.{2}' -f $vm.Groups[1].Value, $vm.Groups[2].Value, $vm.Groups[3].Value
    if ($vm.Groups[4].Value -ne '0') { "$v.$($vm.Groups[4].Value)" } else { $v }
} else { 'unknown' }
$commit = (& git -C $Ffms2Dir rev-parse --short HEAD).Trim()

Info "built ffms2 $version ($commit) against ffmpeg $installedVersion -> $OutDir"
if ($env:GITHUB_OUTPUT) {
    "version=$version-$commit-ffmpeg$installedVersion" | Out-File $env:GITHUB_OUTPUT -Append
}

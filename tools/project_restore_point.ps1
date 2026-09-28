param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Create', 'Verify', 'Restore')][string]$Action,
    [Parameter(Mandatory = $true)][string]$Snapshot,
    [string]$Source,
    [string]$Destination
)

$ErrorActionPreference = 'Stop'
$env:GIT_OPTIONAL_LOCKS = '0'

if (-not ('RestorePoint.FileTree' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Collections.Generic;
using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
namespace RestorePoint {
    public class Entry {
        public string path { get; set; }
        public long bytes { get; set; }
        public string sha256 { get; set; }
    }
    public class Tree {
        public Entry[] files { get; set; }
        public string[] directories { get; set; }
    }
    public static class FileTree {
        private class Reader : IDisposable {
            public byte[] buffer = new byte[1024 * 1024];
            public SHA256 hash = SHA256.Create();
            public void Dispose() { hash.Dispose(); }
        }
        public static Tree Capture(string root, bool excludeGit) {
            string prefix = Path.GetFullPath(root).TrimEnd('\\', '/') + Path.DirectorySeparatorChar;
            var paths = new List<string>();
            var entries = new ConcurrentBag<Entry>();
            var directories = new List<string>();
            var pending = new Stack<DirectoryInfo>();
            pending.Push(new DirectoryInfo(root));
            while (pending.Count > 0) {
                foreach (FileSystemInfo item in pending.Pop().EnumerateFileSystemInfos()) {
                    string relative = item.FullName.Substring(prefix.Length).Replace('\\', '/');
                    if (excludeGit && (relative == ".git" || relative.StartsWith(".git/", StringComparison.Ordinal))) continue;
                    if ((item.Attributes & FileAttributes.ReparsePoint) != 0)
                        throw new IOException("Linked/placeholder path needs explicit handling: " + relative);
                    if ((item.Attributes & FileAttributes.Directory) != 0) {
                        directories.Add(relative);
                        pending.Push((DirectoryInfo)item);
                    } else {
                        paths.Add(item.FullName);
                    }
                }
            }
            long total = 0;
            int completed = 0;
            Parallel.ForEach<string, Reader>(paths, new ParallelOptions { MaxDegreeOfParallelism = 4 },
                () => new Reader(),
                (path, loop, reader) => {
                    long count = 0;
                    reader.hash.Initialize();
                    using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, reader.buffer.Length, FileOptions.SequentialScan)) {
                        int read;
                        while ((read = stream.Read(reader.buffer, 0, reader.buffer.Length)) > 0) {
                            reader.hash.TransformBlock(reader.buffer, 0, read, reader.buffer, 0);
                            count += read;
                        }
                    }
                    reader.hash.TransformFinalBlock(new byte[0], 0, 0);
                    entries.Add(new Entry { path = path.Substring(prefix.Length).Replace('\\', '/'), bytes = count, sha256 = BitConverter.ToString(reader.hash.Hash).Replace("-", "") });
                    Interlocked.Add(ref total, count);
                    int done = Interlocked.Increment(ref completed);
                    if (done % 10000 == 0) Console.WriteLine("Hashed " + done + " / " + paths.Count + " files in " + root);
                    return reader;
                }, reader => reader.Dispose());
            var files = new List<Entry>(entries);
            files.Sort((left, right) => StringComparer.Ordinal.Compare(left.path, right.path));
            directories.Sort(StringComparer.Ordinal);
            return new Tree { files = files.ToArray(), directories = directories.ToArray() };
        }
    }
}
'@
}

function Write-Json($Path, $Value) {
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
}

function Git-Text([string[]]$GitArgs) {
    $lines = @(& git @GitArgs)
    if ($LASTEXITCODE -ne 0) { throw "Git failed with exit code $LASTEXITCODE" }
    return ($lines -join "`n")
}

function Private-Directory([string]$Path) {
    New-Item -ItemType Directory -Path $Path -ErrorAction Stop | Out-Null
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls $Path '/inheritance:r' '/grant:r' "*$($sid):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict snapshot permissions' }
}

function Copy-Tree([string]$From, [string]$To, [string[]]$Extra = @()) {
    $arguments = @($From, $To, '/E', '/COPY:DAT', '/DCOPY:DAT', '/R:1', '/W:1', '/XJ', '/MT:8', '/NP', '/NFL', '/NDL', '/NJH', '/NJS') + $Extra
    & robocopy @arguments | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Robocopy failed with exit code $LASTEXITCODE" }
}

function Inventory([string]$Root, [switch]$ExcludeGit) {
    return [RestorePoint.FileTree]::Capture($Root, [bool]$ExcludeGit)
}

function Assert-Inventory($Expected, $Actual, [string]$Label) {
    if ($Expected.files.Count -ne $Actual.files.Count -or $Expected.directories.Count -ne $Actual.directories.Count) {
        throw "Content or directory mismatch: $Label"
    }
    for ($i = 0; $i -lt $Expected.files.Count; $i++) {
        $left = $Expected.files[$i]; $right = $Actual.files[$i]
        if ($left.path -cne $right.path -or $left.bytes -ne $right.bytes -or $left.sha256 -cne $right.sha256) {
            throw "Content mismatch: $Label / $($left.path)"
        }
    }
    for ($i = 0; $i -lt $Expected.directories.Count; $i++) {
        if ($Expected.directories[$i] -cne $Actual.directories[$i]) { throw "Directory mismatch: $Label" }
    }
}

function Check-Snapshot([string]$Root) {
    $completePath = Join-Path $Root 'COMPLETE.json'
    if (Test-Path -LiteralPath (Join-Path $Root 'INCOMPLETE')) { throw 'Snapshot creation did not finish' }
    if (-not (Test-Path -LiteralPath $completePath)) { throw 'Snapshot has no completion certificate' }
    $certificate = Get-Content -LiteralPath $completePath -Raw | ConvertFrom-Json
    if ($certificate.contract -ne 'local_project_restore_point_v1') { throw 'Unknown snapshot contract' }
    foreach ($name in @('project', 'git-common', 'git-worktree', 'repository.git')) {
        $manifestPath = Join-Path $Root "metadata/$name.json"
        if ((Get-FileHash -LiteralPath $manifestPath).Hash -ne $certificate.manifest_hashes.$name) {
            throw "Manifest changed: $name"
        }
        Write-Host "Verifying $name"
        $expected = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        Assert-Inventory $expected (Inventory (Join-Path $Root $name)) $name
    }
    return $certificate
}

$snapshotPath = [IO.Path]::GetFullPath($Snapshot).TrimEnd('\', '/')
if ($Action -eq 'Create') {
    if (-not $Source) { throw 'Create requires Source' }
    if (Test-Path -LiteralPath $snapshotPath) { throw 'Snapshot destination already exists' }
    $sourcePath = (Resolve-Path -LiteralPath $Source).Path.TrimEnd('\', '/')
    if ($snapshotPath.StartsWith($sourcePath + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Snapshot must be outside the source project'
    }
    $gitRoot = Git-Text @('-C', $sourcePath, 'rev-parse', '--show-toplevel')
    if ([IO.Path]::GetFullPath($gitRoot).TrimEnd('\') -ne $sourcePath) { throw 'Source is not the Git root' }
    $gitDir = Git-Text @('-C', $sourcePath, 'rev-parse', '--absolute-git-dir')
    $commonDir = Git-Text @('-C', $sourcePath, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    if (Test-Path -LiteralPath (Join-Path $commonDir 'objects/info/alternates')) { throw 'External Git object store needs explicit handling' }
    if (Test-Path -LiteralPath (Join-Path $sourcePath '.gitmodules')) { throw 'Submodules need explicit handling' }
    $lockFiles = @(Get-ChildItem -LiteralPath $commonDir -Recurse -Force -File -Filter '*.lock')
    if ($lockFiles.Count) { throw 'Git lock files present; retry once the owner has finished' }
    $sourceFiles = @(Get-ChildItem -LiteralPath $sourcePath -Recurse -Force -File)
    $sourceBytes = ($sourceFiles | Measure-Object Length -Sum).Sum
    $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($snapshotPath))
    if ($drive.AvailableFreeSpace -lt ($sourceBytes + 2GB)) { throw 'Not enough destination disk space' }
    Private-Directory $snapshotPath
    New-Item -ItemType Directory -Path (Join-Path $snapshotPath 'metadata') | Out-Null
    [IO.File]::WriteAllText((Join-Path $snapshotPath 'INCOMPLETE'), 'Creation and verification in progress')
    $statusArgs = @('-c', 'core.fsmonitor=false', '-C', $sourcePath, 'status', '--porcelain=v1', '--untracked-files=all')
    $statusBefore = Git-Text $statusArgs
    $headBefore = Git-Text @('-C', $sourcePath, 'rev-parse', 'HEAD')
    $branchBefore = Git-Text @('-C', $sourcePath, 'symbolic-ref', '--short', 'HEAD')
    Write-Json (Join-Path $snapshotPath 'metadata/source.json') @{
        source = $sourcePath; git_dir = $gitDir; common_git_dir = $commonDir
        head = $headBefore; branch = $branchBefore; created_utc = [DateTime]::UtcNow.ToString('o')
        git_status = $statusBefore; git_version = (Git-Text @('--version'))
    }
    $roots = [ordered]@{ 'project' = $sourcePath; 'git-common' = $commonDir; 'git-worktree' = $gitDir }
    foreach ($name in $roots.Keys) {
        Write-Host "Capturing $name"
        $before = Inventory $roots[$name]
        Copy-Tree $roots[$name] (Join-Path $snapshotPath $name)
        $copied = Inventory (Join-Path $snapshotPath $name)
        Assert-Inventory $before $copied "$name copied bytes"
        Write-Json (Join-Path $snapshotPath "metadata/$name.json") $before
    }
    foreach ($name in $roots.Keys) {
        Write-Host "Checking source stability: $name"
        $before = Get-Content -LiteralPath (Join-Path $snapshotPath "metadata/$name.json") -Raw | ConvertFrom-Json
        Assert-Inventory $before (Inventory $roots[$name]) "$name source stability"
    }
    $standalone = Join-Path $snapshotPath 'repository.git'
    Copy-Tree (Join-Path $snapshotPath 'git-common') $standalone @('/XD', (Join-Path $snapshotPath 'git-common/worktrees'))
    if ($gitDir -ne $commonDir) {
        Copy-Tree (Join-Path $snapshotPath 'git-worktree') $standalone @('/XF', 'commondir', 'gitdir', 'locked')
    }
    $copiedStatus = Git-Text @('-c', 'core.fsmonitor=false', '--git-dir', $standalone, '--work-tree', (Join-Path $snapshotPath 'project'), 'status', '--porcelain=v1', '--untracked-files=all')
    if ($copiedStatus -cne $statusBefore) { throw 'Standalone Git does not reproduce source status' }
    if ((Git-Text $statusArgs) -cne $statusBefore) { throw 'Source Git status changed during snapshot' }
    if ((Git-Text @('-C', $sourcePath, 'rev-parse', 'HEAD')) -ne $headBefore) { throw 'Source HEAD changed during snapshot' }
    Write-Json (Join-Path $snapshotPath 'metadata/repository.git.json') (Inventory $standalone)
    Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $snapshotPath 'project_restore_point.ps1')
    $manifestHashes = [ordered]@{}
    foreach ($name in @('project', 'git-common', 'git-worktree', 'repository.git')) {
        $manifestHashes[$name] = (Get-FileHash -LiteralPath (Join-Path $snapshotPath "metadata/$name.json")).Hash
    }
    Write-Json (Join-Path $snapshotPath 'COMPLETE.json') @{
        contract = 'local_project_restore_point_v1'; verified_utc = [DateTime]::UtcNow.ToString('o')
        head = $headBefore; branch = $branchBefore; source_files = $sourceFiles.Count
        source_bytes = $sourceBytes; git_status_verified = $true; manifest_hashes = $manifestHashes
        scope = 'Local project bytes including ignored files, original Git stores and standalone recovery Git. External VM, external input paths and host-installed runtimes are not snapshotted.'
    }
    Remove-Item -LiteralPath (Join-Path $snapshotPath 'INCOMPLETE')
    Write-Host "VERIFIED SNAPSHOT: $snapshotPath"
} elseif ($Action -eq 'Verify') {
    $certificate = Check-Snapshot $snapshotPath
    Write-Host "VERIFIED SNAPSHOT: $snapshotPath ($($certificate.source_files) project files)"
} else {
    if (-not $Destination) { throw 'Restore requires Destination' }
    $destinationPath = [IO.Path]::GetFullPath($Destination).TrimEnd('\', '/')
    if (Test-Path -LiteralPath $destinationPath) { throw 'Restore destination must not exist; preserve current work in its existing directory' }
    if ($destinationPath.StartsWith($snapshotPath + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Restore outside the frozen snapshot' }
    $certificate = Check-Snapshot $snapshotPath
    $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($destinationPath))
    if ($drive.AvailableFreeSpace -lt ($certificate.source_bytes + 1GB)) { throw 'Not enough restoration disk space' }
    Private-Directory $destinationPath
    Copy-Tree (Join-Path $snapshotPath 'project') $destinationPath @('/XF', (Join-Path $snapshotPath 'project/.git'))
    Copy-Tree (Join-Path $snapshotPath 'repository.git') (Join-Path $destinationPath '.git')
    Git-Text @('-C', $destinationPath, 'config', '--local', 'core.worktree', $destinationPath) | Out-Null
    $expected = Get-Content -LiteralPath (Join-Path $snapshotPath 'metadata/project.json') -Raw | ConvertFrom-Json
    $expected.files = @($expected.files | Where-Object { $_.path -ne '.git' -and -not $_.path.StartsWith('.git/') })
    $expected.directories = @($expected.directories | Where-Object { $_ -ne '.git' -and -not $_.StartsWith('.git/') })
    Assert-Inventory $expected (Inventory $destinationPath -ExcludeGit) 'restored project'
    $sourceInfo = Get-Content -LiteralPath (Join-Path $snapshotPath 'metadata/source.json') -Raw | ConvertFrom-Json
    $restoredStatus = Git-Text @('-c', 'core.fsmonitor=false', '-C', $destinationPath, 'status', '--porcelain=v1', '--untracked-files=all')
    if ($restoredStatus -cne $sourceInfo.git_status) { throw 'Restored Git status differs' }
    if ((Git-Text @('-C', $destinationPath, 'rev-parse', 'HEAD')) -ne $certificate.head) { throw 'Restored HEAD differs' }
    if ((Git-Text @('-C', $destinationPath, 'symbolic-ref', '--short', 'HEAD')) -ne $certificate.branch) { throw 'Restored branch differs' }
    Write-Json ($destinationPath + '.restoration.json') @{
        contract = 'local_project_restoration_v1'; snapshot = $snapshotPath; destination = $destinationPath
        verified_utc = [DateTime]::UtcNow.ToString('o'); project_bytes_verified = $true
        git_status_verified = $true; head = $certificate.head; branch = $certificate.branch
    }
    Write-Host "VERIFIED RESTORATION: $destinationPath"
}

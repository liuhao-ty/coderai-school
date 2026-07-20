param(
    [int]$Port = 18001,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BaseUrl = "http://127.0.0.1:$Port"
$TestRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("coderai-ppt-conversion-" + [guid]::NewGuid().ToString("N"))
$DataDir = Join-Path $TestRoot "data"
$PptxPath = Join-Path $TestRoot "coderai-conversion-test.pptx"
$PreviewPath = Join-Path $TestRoot "coderai-conversion-test.pdf"
$ApiStdout = Join-Path $TestRoot "api.stdout.log"
$ApiStderr = Join-Path $TestRoot "api.stderr.log"
$ApiProcess = $null
$PowerPoint = $null
$Presentation = $null
$PreviousDataDir = $env:CODERAI_DATA_DIR
$PreviousLibreOfficePath = $env:CODERAI_LIBREOFFICE_PATH

function Find-LibreOffice {
    $candidates = @(
        $env:CODERAI_LIBREOFFICE_PATH,
        "C:\Program Files\LibreOffice\program\soffice.exe",
        "C:\Program Files (x86)\LibreOffice\program\soffice.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    $command = Get-Command soffice.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    throw "LibreOffice was not found. Install it or set CODERAI_LIBREOFFICE_PATH."
}

function Wait-ForApi {
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri "$BaseUrl/api/health" -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) { return }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "The isolated FastAPI server did not become ready."
}

function Invoke-JsonRequest {
    param(
        [ValidateSet("GET", "POST", "PUT", "DELETE")][string]$Method,
        [string]$Path,
        [object]$Body,
        [string]$Token = ""
    )
    $headers = @{}
    if ($Token) { $headers["X-CoderAI-Teacher-Token"] = $Token }
    $request = @{
        Method = $Method
        Uri = "$BaseUrl$Path"
        Headers = $headers
        UseBasicParsing = $true
    }
    if ($null -ne $Body) {
        $json = $Body | ConvertTo-Json -Depth 10 -Compress
        $request["ContentType"] = "application/json; charset=utf-8"
        $request["Body"] = [System.Text.Encoding]::UTF8.GetBytes($json)
    }
    $response = Invoke-WebRequest @request
    if (-not $response.Content) { return $null }
    return ($response.Content | ConvertFrom-Json)
}

function New-TestPresentation {
    $script:PowerPoint = New-Object -ComObject PowerPoint.Application
    $script:Presentation = $script:PowerPoint.Presentations.Add()
    $slide = $script:Presentation.Slides.Add(1, 12)
    $title = $slide.Shapes.AddTextbox(1, 72, 72, 720, 90)
    $title.TextFrame.TextRange.Text = "CoderAI LibreOffice conversion test"
    $body = $slide.Shapes.AddTextbox(1, 72, 180, 720, 180)
    $body.TextFrame.TextRange.Text = "This PPTX is uploaded through the curriculum API and converted to PDF."
    $script:Presentation.SaveAs($PptxPath, 24)
    if (-not (Test-Path -LiteralPath $PptxPath)) { throw "PowerPoint did not create the PPTX fixture." }
}

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

try {
    $LibreOfficePath = Find-LibreOffice
    New-TestPresentation
    $Presentation.Close()
    $Presentation = $null
    $PowerPoint.Quit()
    $PowerPoint = $null

    $env:CODERAI_DATA_DIR = $DataDir
    $env:CODERAI_LIBREOFFICE_PATH = $LibreOfficePath
    $ApiProcess = Start-Process -FilePath "python" `
        -ArgumentList @("-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "$Port") `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $ApiStdout -RedirectStandardError $ApiStderr
    Wait-ForApi

    $adminAuth = Invoke-JsonRequest -Method POST -Path "/api/auth/teacher-login" -Body @{
        username = "admin"
        password = "123456"
    }
    if ($adminAuth.password_change_required) {
        $adminAuth = Invoke-JsonRequest -Method POST -Path "/api/auth/change-teacher-password" -Token $adminAuth.token -Body @{
            current_password = "123456"
            next_password = "LibreOffice#2026"
        }
    }
    $adminToken = $adminAuth.token

    $teacherAccount = Invoke-JsonRequest -Method POST -Path "/api/accounts/teachers" -Token $adminToken -Body @{
        name = "PPT conversion teacher"
        username = "ppt.conversion.teacher"
        role = "teacher"
    }
    $teacherAuth = Invoke-JsonRequest -Method POST -Path "/api/auth/teacher-login" -Body @{
        username = "ppt.conversion.teacher"
        password = $teacherAccount.temporary_password
    }
    $teacherAuth = Invoke-JsonRequest -Method POST -Path "/api/auth/change-teacher-password" -Token $teacherAuth.token -Body @{
        current_password = $teacherAccount.temporary_password
        next_password = "TeacherPpt2026"
    }

    $package = Invoke-JsonRequest -Method POST -Path "/api/course-packages" -Token $adminToken -Body @{
        title = "PPT conversion package"
        description = "Isolated LibreOffice integration test"
        package_version = "1.0.0"
        author_user_id = $adminAuth.user.id
        school_stages = @("primary_lower", "primary_upper", "secondary")
        cover_path = ""
    }
    $packageId = $package.package.id
    $course = Invoke-JsonRequest -Method POST -Path "/api/course-packages/$packageId/courses" -Token $adminToken -Body @{
        title = "PPT conversion course"
        description = ""
        order_index = 0
        assignment_instructions = ""
        tool_scope = "text"
        rubric = @(@{ criterion = "completion"; max_score = 100 })
    }
    $courseId = $course.course.id
    $null = Invoke-JsonRequest -Method POST -Path "/api/course-packages/$packageId/publish" -Token $adminToken -Body $null
    $null = Invoke-JsonRequest -Method PUT -Path "/api/course-packages/$packageId/teachers" -Token $adminToken -Body @{
        teacher_ids = @($teacherAccount.account.id)
    }

    $uploadArguments = @(
        "--silent", "--show-error", "--fail-with-body", "--request", "PUT",
        "--header", "X-CoderAI-Teacher-Token: $adminToken",
        "--form", "file=@$PptxPath;type=application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "$BaseUrl/api/curriculum-courses/$courseId/materials/slides"
    )
    $uploadResponse = & curl.exe @uploadArguments
    if ($LASTEXITCODE -ne 0) { throw "PPTX upload failed." }
    $null = $uploadResponse | ConvertFrom-Json

    $material = $null
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $packageState = Invoke-JsonRequest -Method GET -Path "/api/course-packages/$packageId" -Token $adminToken
        $material = $packageState.package.courses[0].materials.slides
        if ($material.conversion_status -eq "ready") { break }
        if ($material.conversion_status -eq "failed") { throw "LibreOffice conversion failed: $($material.conversion_error)" }
        Start-Sleep -Milliseconds 500
    }
    if ($material.conversion_status -ne "ready") { throw "LibreOffice conversion timed out." }

    $previewStatus = & curl.exe --silent --show-error --output $PreviewPath --write-out "%{http_code}" `
        --header "X-CoderAI-Teacher-Token: $($teacherAuth.token)" `
        "$BaseUrl/api/curriculum-courses/$courseId/materials/slides/preview"
    if ($LASTEXITCODE -ne 0 -or $previewStatus -ne "200") { throw "Teacher PDF preview request failed with HTTP $previewStatus." }
    $signature = [System.IO.File]::ReadAllBytes($PreviewPath)[0..3]
    if ([System.Text.Encoding]::ASCII.GetString($signature) -ne "%PDF") { throw "The preview response is not a valid PDF." }

    $downloadStatus = & curl.exe --silent --show-error --output (Join-Path $TestRoot "forbidden-download.json") --write-out "%{http_code}" `
        --header "X-CoderAI-Teacher-Token: $($teacherAuth.token)" `
        "$BaseUrl/api/curriculum-courses/$courseId/materials/slides/download"
    if ($downloadStatus -ne "403") { throw "Teacher slide download should be forbidden, got HTTP $downloadStatus." }

    Write-Host "[OK] LibreOffice: $LibreOfficePath" -ForegroundColor Green
    Write-Host "[OK] PPTX upload converted to a valid PDF preview." -ForegroundColor Green
    Write-Host "[OK] Teacher preview returned HTTP 200 and original download returned HTTP 403." -ForegroundColor Green
    if ($KeepArtifacts) { Write-Host "Artifacts: $TestRoot" -ForegroundColor Yellow }
} finally {
    if ($Presentation) { try { $Presentation.Close() } catch {} }
    if ($PowerPoint) { try { $PowerPoint.Quit() } catch {} }
    if ($Presentation) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($Presentation) }
    if ($PowerPoint) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($PowerPoint) }
    if ($ApiProcess -and -not $ApiProcess.HasExited) { Stop-Process -Id $ApiProcess.Id -Force -ErrorAction SilentlyContinue }
    $env:CODERAI_DATA_DIR = $PreviousDataDir
    $env:CODERAI_LIBREOFFICE_PATH = $PreviousLibreOfficePath
    if (-not $KeepArtifacts -and (Test-Path -LiteralPath $TestRoot)) {
        Remove-Item -LiteralPath $TestRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

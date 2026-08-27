@echo off
title AC-Downloader — Self-Sign and Trust (LOCAL MACHINE ONLY)
echo ===================================================
echo  Code Signing Tool (Self-Signed Certificate)
echo ===================================================
echo.
echo  !! IMPORTANT — READ THIS !!
echo  A self-signed certificate is trusted ONLY on THIS computer
echo  (after you install it into Trusted Root below).
echo.
echo  It will NOT remove SmartScreen / Smart App Control
echo  warnings on OTHER people's machines, and the generated
echo  .pfx file must NEVER be shared or committed to git.
echo.
echo  For public distribution, get a real certificate:
echo    see installer\signing.md  (Certum / SignPath / Azure)
echo.
echo  This tool is for local testing only. Continue?

set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "CERT_SUBJECT=CN=arian13es AC-Downloader, O=arian13es, L=Tabriz, C=IR"
set "PFX_PATH=%~dp0ac-downloader-cert.pfx"
set "PFX_PASS=ACD2026sign"

echo.
echo [1/4] Generating code-signing certificate...
"%PS%" -NoProfile -ExecutionPolicy Bypass -Command ^
  "$cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject '%CERT_SUBJECT%' -CertStoreLocation Cert:\CurrentUser\My -NotAfter (Get-Date).AddYears(5) -KeyAlgorithm RSA -KeyLength 2048 -HashAlgorithm SHA256; ^
   $pwd = ConvertTo-SecureString -String '%PFX_PASS%' -Force -AsPlainText; ^
   Export-PfxCertificate -Cert $cert -FilePath '%PFX_PATH%' -Password $pwd | Out-Null; ^
   Write-Host 'Certificate thumbprint:' $cert.Thumbprint"

if not exist "%PFX_PATH%" (
    echo [ERROR] Certificate generation failed.
    pause
    exit /b 1
)
echo    [OK] Certificate exported to: %PFX_PATH%

echo.
echo [2/4] Installing certificate into Trusted Root store...
echo       (Click YES on the security dialog)
"%PS%" -NoProfile -ExecutionPolicy Bypass -Command ^
  "$pwd = ConvertTo-SecureString -String '%PFX_PASS%' -Force -AsPlainText; ^
   Import-PfxCertificate -FilePath '%PFX_PATH%' -CertStoreLocation Cert:\CurrentUser\Root -Password $pwd | Out-Null; ^
   Import-PfxCertificate -FilePath '%PFX_PATH%' -CertStoreLocation Cert:\CurrentUser\TrustedPublisher -Password $pwd | Out-Null; ^
   Write-Host 'Certificate trusted successfully.'"

echo.
echo [3/4] Locating signtool.exe...

set "SIGNTOOL="

rem Try Windows SDK paths
for /d %%D in ("%ProgramFiles(x86)%\Windows Kits\10\bin\10.*") do (
    if exist "%%D\x64\signtool.exe" set "SIGNTOOL=%%D\x64\signtool.exe"
)
rem Try PATH
if not defined SIGNTOOL (
    where signtool.exe >nul 2>&1
    if %ERRORLEVEL% EQU 0 set "SIGNTOOL=signtool.exe"
)

if not defined SIGNTOOL (
    echo.
    echo [WARNING] signtool.exe not found.
    echo           Install Windows SDK or Visual Studio Build Tools.
    echo           Download: https://developer.microsoft.com/windows/downloads/windows-sdk/
    echo.
    echo           As a fallback, using PowerShell Set-AuthenticodeSignature...
    echo.

    set "EXE_MAIN=%~dp0..\dist\AC-Downloader\AC-Downloader.exe"
    set "EXE_ONEFILE=%~dp0..\dist\AC-Downloader.exe"
    set "EXE_SETUP="
    for %%F in ("%~dp0output\AC-Downloader-Setup-*.exe") do set "EXE_SETUP=%%F"

    "%PS%" -NoProfile -ExecutionPolicy Bypass -Command ^
      "$pwd = ConvertTo-SecureString -String '%PFX_PASS%' -Force -AsPlainText; ^
       $pfx = Get-PfxData -FilePath '%PFX_PATH%' -Password $pwd; ^
       $cert = $pfx.EndEntityCertificates[0]; ^
       $targets = @(); ^
       if (Test-Path '%EXE_MAIN%') { $targets += '%EXE_MAIN%' }; ^
       if (Test-Path '%EXE_ONEFILE%') { $targets += '%EXE_ONEFILE%' }; ^
       if ('%EXE_SETUP%' -ne '' -and (Test-Path '%EXE_SETUP%')) { $targets += '%EXE_SETUP%' }; ^
       foreach ($t in $targets) { ^
         Write-Host 'Signing:' $t; ^
         Set-AuthenticodeSignature -FilePath $t -Certificate $cert -TimestampServer 'http://timestamp.digicert.com' -HashAlgorithm SHA256 | Out-Null; ^
       }; ^
       Write-Host 'Done (PowerShell fallback).'"

    goto :verify
)

echo    [OK] Found: %SIGNTOOL%

echo.
echo [4/4] Signing executables...

set "EXE_MAIN=%~dp0..\dist\AC-Downloader\AC-Downloader.exe"
set "EXE_ONEFILE=%~dp0..\dist\AC-Downloader.exe"

if exist "%EXE_MAIN%" (
    echo    Signing dist\AC-Downloader\AC-Downloader.exe ...
    "%SIGNTOOL%" sign /fd SHA256 /td SHA256 /tr http://timestamp.digicert.com /f "%PFX_PATH%" /p "%PFX_PASS%" "%EXE_MAIN%"
)
if exist "%EXE_ONEFILE%" (
    echo    Signing dist\AC-Downloader.exe ...
    "%SIGNTOOL%" sign /fd SHA256 /td SHA256 /tr http://timestamp.digicert.com /f "%PFX_PATH%" /p "%PFX_PASS%" "%EXE_ONEFILE%"
)

for %%F in ("%~dp0output\AC-Downloader-Setup-*.exe") do (
    echo    Signing %%~nxF ...
    "%SIGNTOOL%" sign /fd SHA256 /td SHA256 /tr http://timestamp.digicert.com /f "%PFX_PATH%" /p "%PFX_PASS%" "%%F"
)

:verify
echo.
echo ===================================================
echo [DONE] Signing complete.
echo.
echo On THIS machine: SmartScreen / SAC will trust the app.
echo On OTHER machines: share ac-downloader-cert.pfx and
echo   double-click to install it into Trusted Root CA,
echo   OR distribute via the Inno Setup installer.
echo ===================================================
pause

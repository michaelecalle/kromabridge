@echo off
title Kromabridge
cd /d "%~dp0"
rem 1. loopMIDI (les ports virtuels Kromabridge et Controle Harpe) : on le lance s'il ne tourne pas.
tasklist /FI "IMAGENAME eq loopMIDI.exe" | find /I "loopMIDI.exe" >nul
if errorlevel 1 (
  if exist "%ProgramFiles(x86)%\Tobias Erichsen\loopMIDI\loopMIDI.exe" (
    start "" "%ProgramFiles(x86)%\Tobias Erichsen\loopMIDI\loopMIDI.exe"
  ) else if exist "%ProgramFiles%\Tobias Erichsen\loopMIDI\loopMIDI.exe" (
    start "" "%ProgramFiles%\Tobias Erichsen\loopMIDI\loopMIDI.exe"
  )
)
rem 2. Attente du port Kromabridge (20 secondes au plus).
"python\python.exe" -c "import rtmidi,time,sys; t=time.time(); ok=lambda: any('kromabridge' in p.lower() for p in rtmidi.MidiOut().get_ports()); [time.sleep(0.5) for _ in iter(lambda: ok() or time.time()-t>20, True)]; sys.exit(0 if ok() else 1)"
if errorlevel 1 (
  echo.
  echo [!!] Le port Kromabridge n'existe pas dans loopMIDI.
  echo      Ouvrez loopMIDI ^(icone pres de l'horloge^), tapez Kromabridge dans "New port-name" et cliquez sur +.
  echo      Faites de meme avec Controle Harpe. Relancez ensuite Kromabridge.
  echo.
  echo [!!] The Kromabridge port does not exist in loopMIDI.
  echo      Open loopMIDI ^(icon near the clock^), type Kromabridge in "New port-name" and click +.
  echo      Do the same with Controle Harpe. Then start Kromabridge again.
  pause
  exit /b
)
rem 3. Deja lance ? On arrete l'ancien programme (et sa fenetre) avant de relancer.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | ? { $_.CommandLine -like '*kromabridge jouer*' } | ForEach-Object { $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.ParentProcessId); if ($p -and $p.Name -eq 'cmd.exe') { taskkill /T /F /PID $p.ProcessId 2>$null } else { taskkill /T /F /PID $_.ProcessId 2>$null } } | Out-Null; $t = Get-Date; while (((Get-Date) - $t).TotalSeconds -lt 10) { try { (New-Object Net.Sockets.TcpClient('127.0.0.1',8080)).Close(); Start-Sleep -Milliseconds 300 } catch { break } }"
rem 4. Le programme, et la page dans le navigateur.
echo Kromabridge : la page s'ouvre dans votre navigateur (http://localhost:8080).
echo Pour arreter Kromabridge : fermez cette fenetre.
echo.
echo Kromabridge: the page opens in your browser (http://localhost:8080).
echo To stop Kromabridge: close this window.
start "" http://localhost:8080
"python\python.exe" -m kromabridge jouer --port 8080
pause

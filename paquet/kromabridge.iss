; Installateur Windows de Kromabridge (Inno Setup 6).
; Fabrique par paquet/construire.py, qui prepare le dossier {#Staging} et passe
; la version : ISCC /DVersion=1.0 /DStaging=... /DSortie=... kromabridge.iss

#ifndef Version
  #define Version "1.0"
#endif

[Setup]
AppId={{6F2A7C1E-4B1D-4E8A-9C3F-2B7D5E1A9C40}
AppName=Kromabridge
AppVersion={#Version}
AppVerName=Kromabridge {#Version}
AppPublisher=Michael (créé avec Claude)
AppPublisherURL=https://github.com/michaelecalle/kromabridge
AppSupportURL=https://github.com/michaelecalle/kromabridge
AppUpdatesURL=https://github.com/michaelecalle/kromabridge/releases
DefaultDirName={autopf}\Kromabridge
DefaultGroupName=Kromabridge
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#Sortie}
OutputBaseFilename=Kromabridge-Setup
SetupIconFile={#Staging}\kromalaser.ico
UninstallDisplayIcon={app}\kromalaser.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
fr.TacheBureau=Créer un raccourci « Kromabridge » sur le bureau
en.TacheBureau=Create a « Kromabridge » shortcut on the desktop
fr.TachePorts=Créer les ports « Kromabridge » et « Controle Harpe » dans loopMIDI
en.TachePorts=Create the « Kromabridge » and « Controle Harpe » ports in loopMIDI
fr.Raccourcis=Raccourcis et ports MIDI :
en.Raccourcis=Shortcuts and MIDI ports:
fr.LoopTitre=loopMIDI
en.LoopTitre=loopMIDI
fr.LoopSousTitre=Le port MIDI virtuel qui relie Kromabridge à votre logiciel de musique (DAW).
en.LoopSousTitre=The virtual MIDI port that links Kromabridge to your music software (DAW).
fr.LoopOk=✓ loopMIDI est installé sur ce PC. Vous pouvez continuer.
en.LoopOk=✓ loopMIDI is installed on this PC. You can continue.
fr.LoopAbsent=✗ loopMIDI n'est pas encore installé sur ce PC.
en.LoopAbsent=✗ loopMIDI is not installed on this PC yet.
fr.LoopTuto=loopMIDI est un logiciel gratuit de Tobias Erichsen. Il ne peut pas être fourni avec Kromabridge : il s'installe à part, en quelques clics.%n%n1. Cliquez sur « Ouvrir le site de loopMIDI ».%n2. Sur la page qui s'ouvre, cliquez sur le lien de téléchargement de loopMIDI.%n3. Lancez le fichier téléchargé et suivez l'installation (Next, puis Install).%n4. Revenez ici et cliquez sur « Revérifier ».%n%nKromabridge créera ensuite lui-même ses deux ports dans loopMIDI.
en.LoopTuto=loopMIDI is free software by Tobias Erichsen. It cannot be shipped with Kromabridge: it installs separately, in a few clicks.%n%n1. Click « Open the loopMIDI website ».%n2. On the page that opens, click the loopMIDI download link.%n3. Run the downloaded file and follow the installation (Next, then Install).%n4. Come back here and click « Check again ».%n%nKromabridge will then create its two ports in loopMIDI by itself.
fr.LoopSite=Ouvrir le site de loopMIDI
en.LoopSite=Open the loopMIDI website
fr.LoopReverifier=Revérifier
en.LoopReverifier=Check again
fr.LoopSansLui=loopMIDI n'est pas installé. Kromabridge ne pourra pas envoyer ses notes à votre logiciel de musique tant qu'il ne l'est pas.%n%nContinuer quand même ? (Vous pourrez installer loopMIDI plus tard, puis relancer cet installateur.)
en.LoopSansLui=loopMIDI is not installed. Kromabridge cannot send its notes to your music software until it is.%n%nContinue anyway? (You can install loopMIDI later, then run this installer again.)
fr.LoopFermer=Pour créer les ports, loopMIDI doit être fermé.%n%nFermez-le : clic droit sur son icône près de l'horloge, puis « Exit ». Cliquez ensuite sur Réessayer.%n%n(Annuler : les ports ne seront pas créés ; vous pourrez les créer à la main dans loopMIDI.)
en.LoopFermer=loopMIDI must be closed to create the ports.%n%nClose it: right-click its icon near the clock, then « Exit ». Then click Retry.%n%n(Cancel: the ports will not be created; you can create them by hand in loopMIDI.)
fr.OuvrirFiche=Ouvrir la fiche d'aide (à imprimer)
en.OuvrirFiche=Open the quick reference (to print)
fr.OuvrirManuel=Ouvrir le manuel
en.OuvrirManuel=Open the manual
fr.OuvrirPi=Ouvrir le guide d'installation de la version Raspberry Pi
en.OuvrirPi=Open the Raspberry Pi version installation guide
fr.Lancer=Lancer Kromabridge
en.Lancer=Start Kromabridge
fr.Manuel=Manuel Kromabridge
en.Manuel=Kromabridge manual

[Tasks]
Name: "bureau"; Description: "{cm:TacheBureau}"; GroupDescription: "{cm:Raccourcis}"
Name: "ports"; Description: "{cm:TachePorts}"; GroupDescription: "{cm:Raccourcis}"

[Files]
Source: "{#Staging}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Kromabridge"; Filename: "{app}\Kromabridge.bat"; WorkingDir: "{app}"; IconFilename: "{app}\kromalaser.ico"
Name: "{autoprograms}\{cm:Manuel}"; Filename: "{app}\doc\Manuel Kromabridge (FR).html"; Languages: fr
Name: "{autoprograms}\{cm:Manuel}"; Filename: "{app}\doc\Kromabridge Manual (EN).html"; Languages: en
Name: "{autodesktop}\Kromabridge"; Filename: "{app}\Kromabridge.bat"; WorkingDir: "{app}"; IconFilename: "{app}\kromalaser.ico"; Tasks: bureau

[Run]
Filename: "{app}\doc\Fiche Kromabridge (FR).html"; Description: "{cm:OuvrirFiche}"; Flags: postinstall shellexec skipifsilent; Languages: fr
Filename: "{app}\doc\Kromabridge Quick Reference (EN).html"; Description: "{cm:OuvrirFiche}"; Flags: postinstall shellexec skipifsilent; Languages: en
Filename: "{app}\doc\Manuel Kromabridge (FR).html"; Description: "{cm:OuvrirManuel}"; Flags: postinstall shellexec skipifsilent unchecked; Languages: fr
Filename: "{app}\doc\Kromabridge Manual (EN).html"; Description: "{cm:OuvrirManuel}"; Flags: postinstall shellexec skipifsilent unchecked; Languages: en
Filename: "{app}\doc\Installer la version Raspberry Pi (FR).html"; Description: "{cm:OuvrirPi}"; Flags: postinstall shellexec skipifsilent unchecked; Languages: fr
Filename: "{app}\doc\Installing the Raspberry Pi version (EN).html"; Description: "{cm:OuvrirPi}"; Flags: postinstall shellexec skipifsilent unchecked; Languages: en
Filename: "{app}\Kromabridge.bat"; WorkingDir: "{app}"; Description: "{cm:Lancer}"; Flags: postinstall shellexec skipifsilent unchecked

[UninstallDelete]
; Le programme seulement : les morceaux et reglages restent dans Documents\Kromabridge.
Type: filesandordirs; Name: "{app}"

[Code]
const
  CLE_PORTS = 'Software\Tobias Erichsen\loopMIDI\Ports';

var
  PageLoop: TWizardPage;
  EtatLoop: TNewStaticText;

function CheminLoopMIDI(): String;
begin
  Result := '';
  if FileExists(ExpandConstant('{commonpf32}\Tobias Erichsen\loopMIDI\loopMIDI.exe')) then
    Result := ExpandConstant('{commonpf32}\Tobias Erichsen\loopMIDI\loopMIDI.exe')
  else if FileExists(ExpandConstant('{commonpf64}\Tobias Erichsen\loopMIDI\loopMIDI.exe')) then
    Result := ExpandConstant('{commonpf64}\Tobias Erichsen\loopMIDI\loopMIDI.exe');
end;

procedure MajEtatLoop();
begin
  if CheminLoopMIDI() <> '' then
    EtatLoop.Caption := CustomMessage('LoopOk')
  else
    EtatLoop.Caption := CustomMessage('LoopAbsent');
end;

procedure OuvrirSiteLoop(Sender: TObject);
var
  Code: Integer;
begin
  ShellExec('open', 'https://www.tobias-erichsen.de/software/loopmidi.html', '', '', SW_SHOWNORMAL, ewNoWait, Code);
end;

procedure Reverifier(Sender: TObject);
begin
  MajEtatLoop();
end;

procedure InitializeWizard();
var
  Tuto: TNewStaticText;
  Site, Revoir: TNewButton;
begin
  PageLoop := CreateCustomPage(wpSelectTasks, CustomMessage('LoopTitre'), CustomMessage('LoopSousTitre'));
  EtatLoop := TNewStaticText.Create(PageLoop);
  EtatLoop.Parent := PageLoop.Surface;
  EtatLoop.Font.Style := [fsBold];
  EtatLoop.Top := 0;
  EtatLoop.Width := PageLoop.SurfaceWidth;
  Tuto := TNewStaticText.Create(PageLoop);
  Tuto.Parent := PageLoop.Surface;
  Tuto.Top := ScaleY(28);
  Tuto.Width := PageLoop.SurfaceWidth;
  Tuto.WordWrap := True;
  Tuto.AutoSize := True;
  Tuto.Caption := CustomMessage('LoopTuto');
  Site := TNewButton.Create(PageLoop);
  Site.Parent := PageLoop.Surface;
  Site.Top := Tuto.Top + Tuto.Height + ScaleY(14);
  Site.Width := ScaleX(220);
  Site.Height := ScaleY(28);
  Site.Caption := CustomMessage('LoopSite');
  Site.OnClick := @OuvrirSiteLoop;
  Revoir := TNewButton.Create(PageLoop);
  Revoir.Parent := PageLoop.Surface;
  Revoir.Top := Site.Top;
  Revoir.Left := Site.Left + Site.Width + ScaleX(10);
  Revoir.Width := ScaleX(140);
  Revoir.Height := ScaleY(28);
  Revoir.Caption := CustomMessage('LoopReverifier');
  Revoir.OnClick := @Reverifier;
  MajEtatLoop();
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = PageLoop.ID then
    MajEtatLoop();
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = PageLoop.ID) and (CheminLoopMIDI() = '') then
    Result := MsgBox(CustomMessage('LoopSansLui'), mbConfirmation, MB_YESNO) = IDYES;
end;

function LoopMIDITourne(): Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/c tasklist /FI "IMAGENAME eq loopMIDI.exe" | find /I "loopMIDI.exe" >nul',
                 '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

procedure CreerPorts();
var
  Code: Integer;
begin
  if RegValueExists(HKCU, CLE_PORTS, 'Kromabridge') and RegValueExists(HKCU, CLE_PORTS, 'Controle Harpe') then
    exit;
  while LoopMIDITourne() do
    if MsgBox(CustomMessage('LoopFermer'), mbInformation, MB_RETRYCANCEL) <> IDRETRY then
      exit;
  RegWriteDWordValue(HKCU, CLE_PORTS, 'Kromabridge', 1);
  RegWriteDWordValue(HKCU, CLE_PORTS, 'Controle Harpe', 1);
  if CheminLoopMIDI() <> '' then
    Exec(CheminLoopMIDI(), '', '', SW_SHOWMINNOACTIVE, ewNoWait, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { Kromabridge deja lance (mise a jour) : on l'arrete, sinon ses fichiers sont verrouilles. }
  Exec('powershell.exe', '-NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name=''python.exe''\" | ? { $_.CommandLine -like ''*kromabridge jouer*'' } | % { taskkill /T /F /PID $_.ProcessId }"',
       '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('ports') then
    CreerPorts();
end;

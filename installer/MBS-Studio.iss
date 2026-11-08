; M.B.S Studio setup wizard (Hebrew, right-to-left).
; Build:  ISCC installer\MBS-Studio.iss   ->  dist\MBS-Studio-Setup-<version>.exe
; The app opens in the browser (python -m src.web); the PyQt desktop GUI is no longer installed by default.
;
; The wizard explains the system, checks the computer, asks where to install and which parts,
; collects the service keys into .env (with a guide link next to every field), copies the app,
; and then runs installer\bootstrap.ps1 to install Python, packages, ffmpeg and the models.

#define AppName "M.B.S Studio"
#define AppVersion "4.1.0"

[Setup]
AppId={{6F2B7C1E-3A9D-4E58-B1C7-9D2E4F6A8B30}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=M.B.S
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\MBS-Studio
DisableDirPage=no
DisableProgramGroupPage=yes
DisableWelcomePage=no
UsePreviousAppDir=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=MBS-Studio-Setup-{#AppVersion}
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\assets\app.ico
UninstallDisplayName={#AppName}
InfoBeforeFile=info.he.txt
WizardStyle=modern
WizardSizePercent=120
Compression=lzma2/max
SolidCompression=yes
ExtraDiskSpaceRequired=3000000000
ShowLanguageDialog=no
; Signing is optional: installer\build.ps1 -SignCommand '...' defines SIGN and the 'mbs' sign tool.
; Windows Smart App Control blocks unsigned installers, so release builds must be signed.
#ifdef SIGN
SignTool=mbs
SignedUninstaller=yes
#endif

[Languages]
Name: "he"; MessagesFile: "compiler:Languages\Hebrew.isl"

[Types]
Name: "full"; Description: "התקנה מלאה (מומלץ)"
Name: "custom"; Description: "התקנה מותאמת"; Flags: iscustom

[Components]
Name: "core"; Description: "M.B.S Studio: כתיבת תסריטים, קולות ווידאו (חובה)"; Types: full custom; Flags: fixed
Name: "transcriber"; Description: "מתמלל וידאו לעברית (ivrit.ai), רץ על המחשב שלך"; Types: full; ExtraDiskSpaceRequired: 600000000
Name: "transcriber\model"; Description: "להוריד את מודל התמלול כבר עכשיו (כ-1.6GB)"; Types: full; ExtraDiskSpaceRequired: 1700000000
Name: "localllm"; Description: "מודל מקומי לבדיקת עובדות וניקוי (Ollama + gemma3:12b, כ-8GB, מומלץ עם כרטיס NVIDIA)"; Types: full; ExtraDiskSpaceRequired: 8500000000

[Tasks]
Name: "desktopicon"; Description: "קיצורי דרך בשולחן העבודה"; GroupDescription: "קיצורי דרך:"

[Files]
Source: "..\src\*"; DestDir: "{app}\src"; Excludes: "__pycache__,*.pyc,*.egg-info"; Flags: recursesubdirs ignoreversion
Source: "..\scripts\*"; DestDir: "{app}\scripts"; Excludes: "__pycache__,*.pyc,_tmp_*"; Flags: recursesubdirs ignoreversion
Source: "..\assets\*"; DestDir: "{app}\assets"; Excludes: "c__Users_*"; Flags: recursesubdirs ignoreversion
Source: "..\data\*"; DestDir: "{app}\data"; Excludes: "eval"; Flags: recursesubdirs ignoreversion
Source: "..\docs\*"; DestDir: "{app}\docs"; Flags: recursesubdirs ignoreversion
Source: "..\requirements*.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\pyproject.toml"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\run_gui.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LOGO.JPEG"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\.env.example"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.he.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "bootstrap.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "constraints.txt"; DestDir: "{app}\installer"; Flags: ignoreversion
; The guide is also unpacked to a temp folder so its help links work before anything is installed
Source: "..\docs\guide.he.html"; Flags: dontcopy

[Dirs]
Name: "{app}\outputs"
Name: "{app}\logs"

[Icons]
Name: "{autoprograms}\{#AppName}\M.B.S Studio"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-m src.web"; WorkingDir: "{app}"; IconFilename: "{app}\assets\app.ico"
Name: "{autoprograms}\{#AppName}\תמלול וידאו לעברית"; Filename: "{app}\.venv\Scripts\python.exe"; Parameters: "-m scripts.transcribe"; WorkingDir: "{app}"; IconFilename: "{app}\assets\app.ico"; Components: transcriber
Name: "{autoprograms}\{#AppName}\בדיקת מערכת"; Filename: "{cmd}"; Parameters: "/k """"{app}\.venv\Scripts\python.exe"" -m scripts.doctor"""; WorkingDir: "{app}"
Name: "{autoprograms}\{#AppName}\עריכת הגדרות"; Filename: "{win}\notepad.exe"; Parameters: """{app}\.env"""
Name: "{autoprograms}\{#AppName}\מדריך המערכת"; Filename: "{app}\docs\guide.he.html"
Name: "{autoprograms}\{#AppName}\תיקון התקנה"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\bootstrap.ps1"" -InstallDir ""{app}"" -Repair"; WorkingDir: "{app}"
Name: "{autoprograms}\{#AppName}\הסרת M.B.S Studio"; Filename: "{uninstallexe}"
Name: "{autodesktop}\M.B.S Studio"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-m src.web"; WorkingDir: "{app}"; IconFilename: "{app}\assets\app.ico"; Tasks: desktopicon
Name: "{autodesktop}\תמלול וידאו לעברית"; Filename: "{app}\.venv\Scripts\python.exe"; Parameters: "-m scripts.transcribe"; WorkingDir: "{app}"; IconFilename: "{app}\assets\app.ico"; Tasks: desktopicon; Components: transcriber

[Run]
Filename: "{app}\docs\guide.he.html"; Description: "לפתוח את מדריך המערכת"; Flags: postinstall shellexec skipifsilent
Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-m src.web"; WorkingDir: "{app}"; Description: "להפעיל את M.B.S Studio"; Flags: postinstall nowait skipifsilent unchecked; Check: BootstrapSucceeded

[UninstallDelete]
; Installed by bootstrap.ps1, not by the wizard. outputs\ and .env are kept.
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\models"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\src"
Type: filesandordirs; Name: "{app}\scripts"
Type: files; Name: "{app}\installer\options.json"

[Code]
type
  TMemoryStatusEx = record
    dwLength: Cardinal;
    dwMemoryLoad: Cardinal;
    ullTotalPhys: Int64;
    ullAvailPhys: Int64;
    ullTotalPageFile: Int64;
    ullAvailPageFile: Int64;
    ullTotalVirtual: Int64;
    ullAvailVirtual: Int64;
    ullAvailExtendedVirtual: Int64;
  end;

function GlobalMemoryStatusEx(var lpBuffer: TMemoryStatusEx): BOOL;
  external 'GlobalMemoryStatusEx@kernel32.dll stdcall';

const
  VOICE_AZURE = 0;
  VOICE_ELEVEN = 1;
  VOICE_BOTH = 2;
  VOICE_GEMINI = 3;
  VOICE_LATER = 4;

var
  RequirementsPage: TOutputMsgMemoWizardPage;
  OpenAIPage: TInputQueryWizardPage;
  VoiceChoicePage: TInputOptionWizardPage;
  VoiceKeysPage: TInputQueryWizardPage;
  ExtrasPage: TInputQueryWizardPage;
  FormatPage: TInputOptionWizardPage;
  FormatCodes: array of String;

  HasInternet, HasNvidia, HasPython, HasFfmpeg, HasOllama: Boolean;
  GpuName: String;
  GpuMemoryMB, RamGB: Integer;
  ComponentsDefaulted, EnvLoaded, GuideExtracted, BootstrapOk: Boolean;

{ ------------------------------------------------------------------ guide links }

procedure OpenGuide(Anchor: String);
var
  Stub: String;
  ErrorCode: Integer;
begin
  if not GuideExtracted then
  begin
    ExtractTemporaryFile('guide.he.html');
    GuideExtracted := True;
  end;
  { Windows drops "#anchor" when opening a local file, so open a stub that redirects to the section }
  Stub := ExpandConstant('{tmp}\open-' + Anchor + '.html');
  SaveStringToFile(Stub, '<meta charset="utf-8"><meta http-equiv="refresh" content="0; url=guide.he.html#' + Anchor + '">', False);
  ShellExec('open', Stub, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

procedure HelpOpenAI(Sender: TObject); begin OpenGuide('azure-openai'); end;
procedure HelpVoices(Sender: TObject); begin OpenGuide('voices'); end;
procedure HelpAzureSpeech(Sender: TObject); begin OpenGuide('azure-speech'); end;
procedure HelpElevenLabs(Sender: TObject); begin OpenGuide('elevenlabs'); end;
procedure HelpGemini(Sender: TObject); begin OpenGuide('gemini'); end;
procedure HelpFormats(Sender: TObject); begin OpenGuide('formats'); end;
procedure HelpRequirements(Sender: TObject); begin OpenGuide('requirements'); end;

procedure AddHelpLink(Page: TWizardPage; Top: Integer; Caption: String; Handler: TNotifyEvent);
var
  Link: TNewStaticText;
begin
  Link := TNewStaticText.Create(Page);
  Link.Parent := Page.Surface;
  Link.AutoSize := False;
  Link.Left := 0;
  Link.Width := Page.SurfaceWidth;
  Link.Top := Top;
  Link.Height := ScaleY(18);
  Link.Caption := Caption;
  Link.Font.Color := clBlue;
  Link.Font.Style := [fsUnderline];
  Link.Cursor := crHand;
  Link.OnClick := Handler;
end;

function BelowLastEdit(Page: TInputQueryWizardPage; LastIndex: Integer): Integer;
var
  Last: TPasswordEdit;
begin
  Last := Page.Edits[LastIndex];
  Result := Last.Top + Last.Height + ScaleY(14);
end;

{ ------------------------------------------------------------------ system checks }

function CheckInternet: Boolean;
var
  Http: Variant;
begin
  Result := False;
  try
    Http := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    Http.SetTimeouts(5000, 5000, 5000, 5000);
    Http.Open('HEAD', 'https://pypi.org/simple/pip/', False);
    Http.Send('');
    Result := (Http.Status >= 200) and (Http.Status < 400);
  except
    Result := False;
  end;
end;

procedure DetectGpu;
var
  OutFile: String;
  Output: AnsiString;
  Line: String;
  Comma, Code: Integer;
begin
  HasNvidia := False;
  GpuName := '';
  GpuMemoryMB := 0;
  OutFile := ExpandConstant('{tmp}\gpu.txt');
  if Exec(ExpandConstant('{cmd}'), '/c nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits > "' + OutFile + '" 2>nul',
          '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0) and LoadStringFromFile(OutFile, Output) then
  begin
    Line := Trim(String(Output));
    if Pos(#13, Line) > 0 then Line := Copy(Line, 1, Pos(#13, Line) - 1);
    if Pos(#10, Line) > 0 then Line := Copy(Line, 1, Pos(#10, Line) - 1);
    Comma := Pos(',', Line);
    if Comma > 0 then
    begin
      GpuName := Trim(Copy(Line, 1, Comma - 1));
      GpuMemoryMB := StrToIntDef(Trim(Copy(Line, Comma + 1, 20)), 0);
      HasNvidia := GpuName <> '';
    end;
  end;
end;

function DetectRamGB: Integer;
var
  Status: TMemoryStatusEx;
begin
  Status.dwLength := 64;
  if GlobalMemoryStatusEx(Status) then
    Result := Round(Status.ullTotalPhys / 1073741824.0)
  else
    Result := 0;
end;

function CommandExists(Command: String): Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/c where ' + Command + ' >nul 2>nul', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

function Mark(Ok: Boolean): String;
begin
  if Ok then Result := '[תקין]  ' else Result := '[שים לב] ';
end;

function RequirementsReport: String;
var
  Version: TWindowsVersion;
  Free, Total: Int64;
  FreeGB: Integer;
  WindowsName, Nl: String;
begin
  Nl := #13#10;
  GetWindowsVersionEx(Version);
  if Version.Build >= 22000 then WindowsName := 'Windows 11' else WindowsName := 'Windows 10';
  FreeGB := 0;
  if GetSpaceOnDisk64(ExpandConstant('{sd}'), Free, Total) then FreeGB := Round(Free / 1073741824.0);

  Result := Mark(True) + 'מערכת הפעלה: ' + WindowsName + ' (build ' + IntToStr(Version.Build) + ')' + Nl;

  if RamGB >= 8 then
    Result := Result + Mark(True) + 'זיכרון: ' + IntToStr(RamGB) + 'GB' + Nl
  else
    Result := Result + Mark(False) + 'זיכרון: ' + IntToStr(RamGB) + 'GB. מומלץ לפחות 8GB, והמודל המקומי לא יסומן.' + Nl;

  if FreeGB >= 20 then
    Result := Result + Mark(True) + 'מקום פנוי בכונן המערכת: ' + IntToStr(FreeGB) + 'GB' + Nl
  else
    Result := Result + Mark(False) + 'מקום פנוי בכונן המערכת: ' + IntToStr(FreeGB) + 'GB. ההתקנה המלאה צריכה כ-20GB, אפשר לוותר על רכיבים או לבחור כונן אחר.' + Nl;

  if HasNvidia then
    Result := Result + Mark(True) + 'כרטיס מסך: ' + GpuName + ' (' + IntToStr(GpuMemoryMB div 1024) + 'GB). התמלול ירוץ על ה-GPU.' + Nl
  else
    Result := Result + Mark(False) + 'לא נמצא כרטיס NVIDIA. התמלול ירוץ על המעבד (איטי יותר), והמודל המקומי לא יסומן.' + Nl;

  if HasInternet then
    Result := Result + Mark(True) + 'חיבור לאינטרנט' + Nl
  else
    Result := Result + '[חסר]   אין חיבור לאינטרנט. ההתקנה מורידה רכיבים ולא תוכל להמשיך בלעדיו.' + Nl;

  Result := Result + Nl + 'רכיבים קיימים במחשב:' + Nl;
  if HasPython then Result := Result + '  Python 3.12: מותקן' + Nl else Result := Result + '  Python 3.12: יותקן אוטומטית' + Nl;
  if HasFfmpeg then Result := Result + '  ffmpeg: מותקן' + Nl else Result := Result + '  ffmpeg: יותקן אוטומטית לתיקיית ההתקנה' + Nl;
  if HasOllama then Result := Result + '  Ollama: מותקן' + Nl else Result := Result + '  Ollama: יותקן אם תבחר במודל המקומי' + Nl;
end;

{ ------------------------------------------------------------------ .env }

function EnvKeyIndex(Lines: TArrayOfString; Key: String): Integer;
var
  I: Integer;
begin
  Result := -1;
  for I := 0 to GetArrayLength(Lines) - 1 do
    if Pos(Key + '=', Trim(Lines[I])) = 1 then
    begin
      Result := I;
      Exit;
    end;
end;

function EnvGet(Lines: TArrayOfString; Key: String): String;
var
  Index: Integer;
begin
  Result := '';
  Index := EnvKeyIndex(Lines, Key);
  if Index >= 0 then
  begin
    Result := Trim(Copy(Trim(Lines[Index]), Length(Key) + 2, 10000));
    if (Length(Result) >= 2) and (Result[1] = '"') and (Result[Length(Result)] = '"') then
      Result := Copy(Result, 2, Length(Result) - 2);
  end;
end;

procedure EnvSet(var Lines: TArrayOfString; Key, Value: String);
var
  Index: Integer;
begin
  Index := EnvKeyIndex(Lines, Key);
  if Index < 0 then
  begin
    Index := GetArrayLength(Lines);
    SetArrayLength(Lines, Index + 1);
  end;
  Lines[Index] := Key + '=' + Value;
end;

{ Typed values replace old ones; an empty field keeps whatever the .env already had }
procedure EnvSetTyped(var Lines: TArrayOfString; Key, Value: String);
begin
  Value := Trim(Value);
  if (Value <> '') or (EnvKeyIndex(Lines, Key) < 0) then
    EnvSet(Lines, Key, Value);
end;

procedure EnvSetDefault(var Lines: TArrayOfString; Key, Value: String);
begin
  if EnvKeyIndex(Lines, Key) < 0 then
    EnvSet(Lines, Key, Value);
end;

function EnvPath: String;
begin
  Result := AddBackslash(WizardDirValue) + '.env';
end;

procedure SetIfEmpty(Page: TInputQueryWizardPage; Index: Integer; Value: String);
begin
  if (Trim(Page.Values[Index]) = '') and (Value <> '') then
    Page.Values[Index] := Value;
end;

{ On a reinstall, show what the existing .env already contains }
procedure LoadExistingEnv;
var
  Lines: TArrayOfString;
  Provider, Format: String;
  I: Integer;
  HasAzure, HasEleven: Boolean;
begin
  EnvLoaded := True;
  if not LoadStringsFromFile(EnvPath, Lines) then Exit;
  SetIfEmpty(OpenAIPage, 0, EnvGet(Lines, 'AZURE_OPENAI_ENDPOINT'));
  SetIfEmpty(OpenAIPage, 1, EnvGet(Lines, 'AZURE_OPENAI_API_KEY'));
  SetIfEmpty(OpenAIPage, 2, EnvGet(Lines, 'AZURE_OPENAI_DEPLOYMENT'));
  if EnvGet(Lines, 'AZURE_OPENAI_API_VERSION') <> '' then OpenAIPage.Values[3] := EnvGet(Lines, 'AZURE_OPENAI_API_VERSION');
  SetIfEmpty(VoiceKeysPage, 0, EnvGet(Lines, 'AZURE_SPEECH_KEY'));
  SetIfEmpty(VoiceKeysPage, 1, EnvGet(Lines, 'AZURE_SPEECH_REGION'));
  SetIfEmpty(VoiceKeysPage, 2, EnvGet(Lines, 'ELEVENLABS_API_KEY'));
  SetIfEmpty(ExtrasPage, 0, EnvGet(Lines, 'GEMINI_API_KEY'));

  HasAzure := EnvGet(Lines, 'AZURE_SPEECH_KEY') <> '';
  HasEleven := EnvGet(Lines, 'ELEVENLABS_API_KEY') <> '';
  Provider := Lowercase(EnvGet(Lines, 'DEFAULT_TTS_PROVIDER'));
  if Provider = 'gemini' then VoiceChoicePage.SelectedValueIndex := VOICE_GEMINI
  else if (Provider = 'elevenlabs') or (HasEleven and not HasAzure) then VoiceChoicePage.SelectedValueIndex := VOICE_ELEVEN
  else if HasAzure and HasEleven then VoiceChoicePage.SelectedValueIndex := VOICE_BOTH
  else if HasAzure then VoiceChoicePage.SelectedValueIndex := VOICE_AZURE;

  Format := EnvGet(Lines, 'PODCAST_FORMAT');
  for I := 0 to GetArrayLength(FormatCodes) - 1 do
    if FormatCodes[I] = Format then FormatPage.SelectedValueIndex := I;
end;

procedure WriteEnv;
var
  Lines: TArrayOfString;
  Provider: String;
begin
  if not LoadStringsFromFile(ExpandConstant('{app}\.env'), Lines) then
  begin
    SetArrayLength(Lines, 2);
    Lines[0] := '# M.B.S Studio settings. Edit, save, and restart the app. Guide: docs\guide.he.html#settings';
    Lines[1] := '';
  end;

  EnvSetTyped(Lines, 'AZURE_OPENAI_ENDPOINT', OpenAIPage.Values[0]);
  EnvSetTyped(Lines, 'AZURE_OPENAI_API_KEY', OpenAIPage.Values[1]);
  EnvSetTyped(Lines, 'AZURE_OPENAI_DEPLOYMENT', OpenAIPage.Values[2]);
  EnvSetTyped(Lines, 'AZURE_OPENAI_API_VERSION', OpenAIPage.Values[3]);
  EnvSetTyped(Lines, 'AZURE_SPEECH_KEY', VoiceKeysPage.Values[0]);
  EnvSetTyped(Lines, 'AZURE_SPEECH_REGION', VoiceKeysPage.Values[1]);
  EnvSetTyped(Lines, 'ELEVENLABS_API_KEY', VoiceKeysPage.Values[2]);
  EnvSetTyped(Lines, 'GEMINI_API_KEY', ExtrasPage.Values[0]);

  case VoiceChoicePage.SelectedValueIndex of
    VOICE_AZURE: Provider := 'azure';
    VOICE_ELEVEN: Provider := 'elevenlabs';
    VOICE_GEMINI: Provider := 'gemini';
  else
    Provider := '';
  end;
  if Provider <> '' then EnvSet(Lines, 'DEFAULT_TTS_PROVIDER', Provider)
  else EnvSetDefault(Lines, 'DEFAULT_TTS_PROVIDER', 'auto');

  EnvSet(Lines, 'PODCAST_FORMAT', FormatCodes[FormatPage.SelectedValueIndex]);
  EnvSetDefault(Lines, 'PODCAST_LANGUAGE', 'he');
  EnvSetDefault(Lines, 'DIALOGUE_ENGINE', 'v2');
  EnvSetDefault(Lines, 'PODCAST_TARGET_MINUTES', '0');
  EnvSetDefault(Lines, 'SCRIPT_MAX_REVISIONS', '1');
  EnvSetDefault(Lines, 'VERIFY_GROUNDING', 'true');
  EnvSetDefault(Lines, 'LOCAL_LLM', 'auto');
  EnvSetDefault(Lines, 'LOCAL_LLM_BASE_URL', 'http://localhost:11434');
  EnvSetDefault(Lines, 'LOCAL_LLM_MODEL', 'gemma3:12b');
  EnvSetDefault(Lines, 'LOCAL_LLM_CONTEXT', '12288');

  { Plain ASCII without a BOM: python-dotenv would read a BOM into the first key }
  SaveStringsToFile(ExpandConstant('{app}\.env'), Lines, False);
end;

{ ------------------------------------------------------------------ bootstrap }

function BootstrapSucceeded: Boolean;
begin
  Result := BootstrapOk;
end;

procedure RunBootstrap;
var
  Params: String;
  Code: Integer;
begin
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\installer\bootstrap.ps1') +
            '" -InstallDir "' + ExpandConstant('{app}') + '"';
  if WizardIsComponentSelected('transcriber') then Params := Params + ' -Transcriber';
  if WizardIsComponentSelected('transcriber\model') then Params := Params + ' -TranscriberModel';
  { /NOGPU=1 on the command line skips the ~1 GB of CUDA libraries (transcription then runs on the CPU) }
  if HasNvidia and (ExpandConstant('{param:NOGPU|0}') <> '1') then Params := Params + ' -Gpu';
  if WizardIsComponentSelected('localllm') then Params := Params + ' -LocalLlm';
  if WizardSilent then Params := Params + ' -NoPause';

  WizardForm.StatusLabel.Caption := 'מתקין Python, חבילות, ffmpeg ומודלים. ההתקדמות מוצגת בחלון נפרד; בהתקנה ראשונה זה לוקח 10 עד 30 דקות.';
  if not Exec('powershell.exe', Params, ExpandConstant('{app}'), SW_SHOW, ewWaitUntilTerminated, Code) then
    Code := 1;

  BootstrapOk := Code <> 1;
  if Code = 2 then
    SuppressibleMsgBox('ההתקנה הושלמה, אבל חלק מהרכיבים האופציונליים לא הותקנו (הפירוט מופיע בחלון ההתקנה).' + #13#10#13#10 +
      'אפשר לנסות שוב מתפריט התחל: M.B.S Studio > תיקון התקנה.' + #13#10 +
      'יומן: ' + ExpandConstant('{app}\logs\install.log'), mbInformation, MB_OK, IDOK)
  else if Code <> 0 then
    SuppressibleMsgBox('התקנת הרכיבים נכשלה.' + #13#10#13#10 +
      'בדוק את החיבור לאינטרנט והרץ מתפריט התחל: M.B.S Studio > תיקון התקנה.' + #13#10 +
      'יומן: ' + ExpandConstant('{app}\logs\install.log'), mbError, MB_OK, IDOK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    WriteEnv;
    RunBootstrap;
  end;
end;

{ ------------------------------------------------------------------ wizard pages }

procedure InitializeWizard;
begin
  HasInternet := CheckInternet;
  DetectGpu;
  RamGB := DetectRamGB;
  HasPython := RegKeyExists(HKCU, 'Software\Python\PythonCore\3.12') or RegKeyExists(HKLM, 'Software\Python\PythonCore\3.12');
  HasFfmpeg := CommandExists('ffmpeg');
  HasOllama := FileExists(ExpandConstant('{localappdata}\Programs\Ollama\ollama.exe')) or CommandExists('ollama');

  RequirementsPage := CreateOutputMsgMemoPage(wpInfoBefore,
    'בדיקת דרישות', 'בדקתי את המחשב שלך. אלה הממצאים:',
    'פריטים שמסומנים "שים לב" לא מונעים התקנה, אבל משפיעים על הרכיבים המומלצים.',
    RequirementsReport);
  AddHelpLink(RequirementsPage, RequirementsPage.RichEditViewer.Top + RequirementsPage.RichEditViewer.Height + ScaleY(6),
    'מה צריך ולמה? פרטים במדריך', @HelpRequirements);
  RequirementsPage.RichEditViewer.Height := RequirementsPage.RichEditViewer.Height - ScaleY(24);

  OpenAIPage := CreateInputQueryPage(wpSelectTasks,
    'Azure OpenAI: כתיבת התסריט',
    'המודל שמנתח את השיעור וכותב את הדיאלוג (חובה).',
    'העתק את הפרטים מהדף "Keys and Endpoint" של המשאב בפורטל של Azure. אפשר להשאיר ריק ולמלא אחר כך.');
  OpenAIPage.Add('Endpoint (כתובת שמתחילה ב-https://):', False);
  OpenAIPage.Add('מפתח (KEY 1):', True);
  OpenAIPage.Add('שם הפריסה (Deployment), כפי שקראת לה ב-Azure:', False);
  OpenAIPage.Add('גרסת API (אפשר להשאיר כמו שהיא):', False);
  OpenAIPage.Values[3] := '2024-02-01';
  AddHelpLink(OpenAIPage, BelowLastEdit(OpenAIPage, 3), 'לא בטוח מה למלא? מדריך צעד אחר צעד: איך משיגים את הפרטים', @HelpOpenAI);

  VoiceChoicePage := CreateInputOptionPage(OpenAIPage.ID,
    'קולות', 'באיזה שירות להפיק את הקולות של רועי ונועה?',
    'מספיק שירות אחד. לא זוכר במה השתמשת? בחר "אמלא אחר כך", ואחרי ההתקנה הפעל "בדיקת מערכת" מתפריט התחל.',
    True, False);
  VoiceChoicePage.Add('Azure Speech (כל שורה מוקלטת לבד, יש מסלול חינמי)');
  VoiceChoicePage.Add('ElevenLabs (שני המנחים כשיחה אחת, כמו NotebookLM, מומלץ)');
  VoiceChoicePage.Add('Azure Speech וגם ElevenLabs');
  VoiceChoicePage.Add('Gemini (שיחה טבעית; את המפתח ממלאים בעמוד אחר כך)');
  VoiceChoicePage.Add('אמלא אחר כך');
  VoiceChoicePage.SelectedValueIndex := VOICE_ELEVEN;
  AddHelpLink(VoiceChoicePage, VoiceChoicePage.CheckListBox.Top + VoiceChoicePage.CheckListBox.Height + ScaleY(8),
    'מה ההבדל בין השירותים?', @HelpVoices);
  VoiceChoicePage.CheckListBox.Height := VoiceChoicePage.CheckListBox.Height - ScaleY(28);

  VoiceKeysPage := CreateInputQueryPage(VoiceChoicePage.ID,
    'מפתחות לקולות', 'מלא את הפרטים של השירות שבחרת. את הקולות של ElevenLabs בוחרים אחרי ההתקנה, באפליקציה: הגדרות > טען קולות מהחשבון.',
    'השדות של שירות שלא בחרת מושבתים.');
  VoiceKeysPage.Add('Azure Speech: מפתח (KEY 1):', True);
  VoiceKeysPage.Add('Azure Speech: אזור (Location/Region), למשל westeurope:', False);
  VoiceKeysPage.Add('ElevenLabs: מפתח API:', True);
  AddHelpLink(VoiceKeysPage, BelowLastEdit(VoiceKeysPage, 2), 'איך משיגים מפתח ל-Azure Speech?', @HelpAzureSpeech);
  AddHelpLink(VoiceKeysPage, BelowLastEdit(VoiceKeysPage, 2) + ScaleY(22), 'איך משיגים מפתח ל-ElevenLabs?', @HelpElevenLabs);

  ExtrasPage := CreateInputQueryPage(VoiceKeysPage.ID,
    'Gemini', 'קולות Gemini (אם בחרת בהם), וגם צ''אט ותמונות.',
    'המערכת עובדת גם בלעדיו, ואפשר להשאיר ריק.');
  ExtrasPage.Add('מפתח Gemini:', True);
  AddHelpLink(ExtrasPage, BelowLastEdit(ExtrasPage, 0), 'איך משיגים מפתח Gemini?', @HelpGemini);

  FormatPage := CreateInputOptionPage(ExtrasPage.ID,
    'סגנון הפודקאסט', 'איזה פורמט להעדיף כברירת מחדל?',
    'במצב אוטומטי, המערכת בוחרת לכל שיעור את הפורמט שמתאים לו. אפשר לשנות בכל עת.',
    True, False);
  FormatPage.Add('אוטומטי: לפי סוג השיעור (מומלץ)');
  FormatPage.Add('צלילה לעומק: מהיסודות ועד הרעיון הכי קשה');
  FormatPage.Add('למתחילים: לאט, עם אנלוגיות מהיומיום');
  FormatPage.Add('הכנה למבחן: הגדרות, הבחנות ושאלות בדיקה');
  FormatPage.Add('סיפור: להיסטוריה, למדעי הרוח ולמקרי בוחן');
  FormatPage.Add('דיון: שני מנחים בשתי גישות');
  SetArrayLength(FormatCodes, 6);
  FormatCodes[0] := 'auto';
  FormatCodes[1] := 'deep_dive';
  FormatCodes[2] := 'beginner_explainer';
  FormatCodes[3] := 'exam_prep';
  FormatCodes[4] := 'story';
  FormatCodes[5] := 'debate';
  FormatPage.SelectedValueIndex := 0;
  AddHelpLink(FormatPage, FormatPage.CheckListBox.Top + FormatPage.CheckListBox.Height + ScaleY(8),
    'הסבר על כל פורמט', @HelpFormats);
  FormatPage.CheckListBox.Height := FormatPage.CheckListBox.Height - ScaleY(28);
end;

procedure CurPageChanged(CurPageID: Integer);
var
  Choice: Integer;
  RecommendLocal: Boolean;
begin
  if (CurPageID = wpSelectComponents) and not ComponentsDefaulted then
  begin
    ComponentsDefaulted := True;
    RecommendLocal := HasNvidia and (GpuMemoryMB >= 7500) and (RamGB >= 12);
    if not RecommendLocal then WizardSelectComponents('!localllm');
  end;

  if (CurPageID = OpenAIPage.ID) and not EnvLoaded then
    LoadExistingEnv;

  if CurPageID = VoiceKeysPage.ID then
  begin
    Choice := VoiceChoicePage.SelectedValueIndex;
    VoiceKeysPage.Edits[0].Enabled := (Choice = VOICE_AZURE) or (Choice = VOICE_BOTH);
    VoiceKeysPage.Edits[1].Enabled := VoiceKeysPage.Edits[0].Enabled;
    VoiceKeysPage.Edits[2].Enabled := (Choice = VOICE_ELEVEN) or (Choice = VOICE_BOTH);
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = VoiceKeysPage.ID) and ((VoiceChoicePage.SelectedValueIndex = VOICE_LATER) or (VoiceChoicePage.SelectedValueIndex = VOICE_GEMINI));
end;

function Confirm(Text: String): Boolean;
begin
  Result := MsgBox(Text, mbConfirmation, MB_YESNO) = IDYES;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Endpoint, Region: String;
  Choice: Integer;
begin
  Result := True;

  if CurPageID = RequirementsPage.ID then
  begin
    if not HasInternet then HasInternet := CheckInternet;
    if not HasInternet then
    begin
      MsgBox('אין חיבור לאינטרנט. ההתקנה מורידה את Python, חבילות ומודלים, ולא יכולה להמשיך בלעדיו.' + #13#10 +
             'התחבר לאינטרנט ולחץ שוב "הבא".', mbError, MB_OK);
      Result := False;
    end;
  end

  else if CurPageID = wpSelectDir then
  begin
    if Length(WizardDirValue) > 90 then
      Result := Confirm('נתיב ההתקנה ארוך (' + IntToStr(Length(WizardDirValue)) + ' תווים). ב-Windows נתיבים ארוכים גורמים לכשלים בהתקנת חבילות.' + #13#10 +
                        'מומלץ נתיב קצר יותר, למשל C:\MBS-Studio. להמשיך בכל זאת?');
  end

  else if CurPageID = OpenAIPage.ID then
  begin
    Endpoint := Lowercase(Trim(OpenAIPage.Values[0]));
    if (Endpoint = '') or (Trim(OpenAIPage.Values[1]) = '') or (Trim(OpenAIPage.Values[2]) = '') then
      Result := Confirm('חסרים פרטי Azure OpenAI. בלעדיהם המערכת לא תוכל לכתוב תסריטים.' + #13#10#13#10 +
                        'להמשיך ולמלא אחר כך (מתפריט התחל: "עריכת הגדרות")?')
    else if Pos('https://', Endpoint) <> 1 then
    begin
      MsgBox('ה-Endpoint צריך להתחיל ב-https://, למשל https://my-resource.openai.azure.com/' + #13#10 +
             'אפשר להעתיק אותו מהדף "Keys and Endpoint" ב-Azure.', mbError, MB_OK);
      Result := False;
    end
    else if (Pos('.openai.azure.com', Endpoint) = 0) and (Pos('.cognitiveservices.azure.com', Endpoint) = 0) and (Pos('.azure.com', Endpoint) = 0) then
      Result := Confirm('הכתובת לא נראית כמו Endpoint של Azure (בדרך כלל היא מסתיימת ב-openai.azure.com). להמשיך בכל זאת?');
  end

  else if CurPageID = VoiceKeysPage.ID then
  begin
    Choice := VoiceChoicePage.SelectedValueIndex;
    if ((Choice = VOICE_AZURE) or (Choice = VOICE_BOTH)) and
       ((Trim(VoiceKeysPage.Values[0]) = '') or (Trim(VoiceKeysPage.Values[1]) = '')) then
      Result := Confirm('חסרים המפתח או האזור של Azure Speech. בלעדיהם לא יופקו קולות ב-Azure. להמשיך ולמלא אחר כך?');
    if Result and ((Choice = VOICE_ELEVEN) or (Choice = VOICE_BOTH)) and (Trim(VoiceKeysPage.Values[2]) = '') then
      Result := Confirm('חסר מפתח ElevenLabs. להמשיך ולמלא אחר כך?');
    Region := Lowercase(Trim(VoiceKeysPage.Values[1]));
    StringChangeEx(Region, ' ', '', True);
    VoiceKeysPage.Values[1] := Region;
  end

  else if CurPageID = ExtrasPage.ID then
  begin
    if (VoiceChoicePage.SelectedValueIndex = VOICE_GEMINI) and (Trim(ExtrasPage.Values[0]) = '') then
      Result := Confirm('בחרת קולות Gemini אבל לא מילאת מפתח Gemini. בלעדיו לא יופקו קולות. להמשיך ולמלא אחר כך?');
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  Voices: String;
begin
  case VoiceChoicePage.SelectedValueIndex of
    VOICE_AZURE: Voices := 'Azure Speech';
    VOICE_ELEVEN: Voices := 'ElevenLabs';
    VOICE_BOTH: Voices := 'Azure Speech + ElevenLabs';
    VOICE_GEMINI: Voices := 'Gemini';
  else
    Voices := 'יוגדר אחר כך';
  end;
  Result := MemoDirInfo + NewLine + NewLine + MemoComponentsInfo + NewLine + NewLine;
  if MemoTasksInfo <> '' then Result := Result + MemoTasksInfo + NewLine + NewLine;
  Result := Result + 'הגדרות:' + NewLine +
    Space + 'Azure OpenAI: ' + Trim(OpenAIPage.Values[2]) + NewLine +
    Space + 'קולות: ' + Voices + NewLine +
    Space + 'פורמט: ' + FormatPage.CheckListBox.ItemCaption[FormatPage.SelectedValueIndex] + NewLine + NewLine +
    'אחרי העתקת הקבצים ייפתח חלון שמתקין את Python, החבילות והמודלים. בהתקנה ראשונה זה לוקח 10 עד 30 דקות.';
end;

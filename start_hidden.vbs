' Starts the Expo Data Tool without any black window. Writes tool_log.txt (for finding problems).
Set sh = CreateObject("WScript.Shell")
folder = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = folder
q = Chr(34)
cmd = "cmd /c " & q & q & folder & "\.venv\Scripts\python.exe" & q & " -m streamlit run app.py > " & q & folder & "\tool_log.txt" & q & " 2>&1" & q
sh.Run cmd, 0, False

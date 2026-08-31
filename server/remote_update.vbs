Set sh = CreateObject("WScript.Shell")
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
cmd = Chr(34) & scriptDir & "\remote_update.cmd" & Chr(34)
sh.Run cmd, 0, False

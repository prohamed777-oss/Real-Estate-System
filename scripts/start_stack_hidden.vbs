' Starts the hidden stack supervisor (no windows appear).
' Auto-starts at logon via the Startup folder.
Set sh = CreateObject("WScript.Shell")
sh.Run """D:\Real Estate System\backend\.venv\Scripts\pythonw.exe"" ""D:\Real Estate System\scripts\supervisor.pyw""", 0, False

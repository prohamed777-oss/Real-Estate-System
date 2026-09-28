@echo off
rem Gracefully stops the whole hidden stack within 10 seconds.
type nul > "D:\Real Estate System\logs\STOP.flag"
echo Stop flag created - supervisor will shut everything down within 10s.

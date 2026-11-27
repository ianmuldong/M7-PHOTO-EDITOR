@echo off
rem Drag one or more photos onto this file to test M7 with your graphics card.
rem Photos are copied to a temporary folder first; your originals are never changed.
"%~dp0M7 Studio.exe" --self-test %*

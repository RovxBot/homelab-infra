#!/bin/bash
set -euo pipefail
cp -a /app/Openbrew.Web/. /runtime/
mcs -target:library -r:System.Web -r:System.Data -r:System.Xml \
  -out:/runtime/bin/HomelabEntraSso.dll /adapter/EntraSso.cs
mcs -r:System.Web -r:System.Data -r:System.Xml \
  -out:/runtime/provision.exe /adapter/EntraSso.cs
export OPENBREW_SA_PASSWORD="$MSSQL_SA_PASSWORD"
mono /app/Openbrew.DbInit/bin/Release/Openbrew.DbInit.exe
mono /runtime/provision.exe
rm /runtime/provision.exe

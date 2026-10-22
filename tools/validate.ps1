param(
    [Parameter(Mandatory=$true)][string]$SchemaDirectory,
    [Parameter(Mandatory=$true)][string]$BaseScriptDirectory
)
$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path -Parent $PSScriptRoot
$schemaRoot = (Resolve-Path -LiteralPath $SchemaDirectory).Path
$nativeRoot = (Resolve-Path -LiteralPath $BaseScriptDirectory).Path
$results = @()
foreach ($kind in @('aiscripts','md','diff')) {
    $schemas = New-Object System.Xml.Schema.XmlSchemaSet
    $schemas.XmlResolver = New-Object System.Xml.XmlUrlResolver
    $schemaSettings = New-Object System.Xml.XmlReaderSettings
    $schemaSettings.DtdProcessing = [System.Xml.DtdProcessing]::Parse
    $schemaSettings.XmlResolver = $null
    $schemaReader = [System.Xml.XmlReader]::Create((Join-Path $schemaRoot "$kind.xsd"), $schemaSettings)
    try { $null = $schemas.Add('', $schemaReader) } finally { $schemaReader.Close() }
    $schemas.Compile()
    foreach ($folder in @('aiscripts','md','libraries','t')) {
        foreach ($file in Get-ChildItem -LiteralPath (Join-Path $sourceRoot $folder) -Recurse -Filter '*.xml') {
            [xml]$document = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
            $required = switch ($document.DocumentElement.LocalName) { 'aiscript' {'aiscripts'} 'mdscript' {'md'} 'diff' {'diff'} default {''} }
            if ($required -ne $kind) { continue }
            $errors = New-Object 'System.Collections.Generic.List[string]'
            $settings = New-Object System.Xml.XmlReaderSettings
            $settings.Schemas = $schemas
            $settings.ValidationType = [System.Xml.ValidationType]::Schema
            $settings.add_ValidationEventHandler({ param($sender,$eventArgs) $errors.Add($eventArgs.Message) })
            $reader = [System.Xml.XmlReader]::Create($file.FullName,$settings)
            try { while ($reader.Read()) {} } finally { $reader.Close() }
            $results += [pscustomobject]@{file=$file.FullName.Substring($sourceRoot.Length+1);schema=$kind;errors=@($errors.ToArray())}
        }
    }
}
$selectors = @()
foreach ($name in @('order.assist.xml','lib.request.orders.xml')) {
    [xml]$original = Get-Content -LiteralPath (Join-Path $nativeRoot $name) -Raw -Encoding UTF8
    [xml]$patch = Get-Content -LiteralPath (Join-Path (Join-Path $sourceRoot 'aiscripts') $name) -Raw -Encoding UTF8
    foreach ($addition in $patch.diff.add) {
        $selectors += [pscustomobject]@{file=$name;selector=$addition.sel;matches=$original.SelectNodes($addition.sel).Count}
    }
}
$report = [pscustomobject]@{schema_validation=$results;base_patch_selectors=$selectors}
$report | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'schema_checks.json') -Encoding UTF8
if (@($results | Where-Object {$_.errors.Count -gt 0}).Count -gt 0) { throw 'Schema validation failed; see tools/schema_checks.json' }
if (@($selectors | Where-Object {$_.matches -ne 1}).Count -gt 0) { throw 'Patch selector validation failed; see tools/schema_checks.json' }
Write-Output ('Validated {0} XML files and {1} native patch selectors.' -f $results.Count,$selectors.Count)

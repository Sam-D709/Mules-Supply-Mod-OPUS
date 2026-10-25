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
$actionDefinitions = @{}
$actionReferences = @()
foreach ($file in Get-ChildItem -LiteralPath (Join-Path $sourceRoot 'aiscripts') -Filter '*.xml') {
    [xml]$document = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
    foreach ($group in $document.SelectNodes('/aiscript/interrupts/library/actions')) {
        if ($actionDefinitions.ContainsKey($group.name)) { throw ('Duplicate action library: ' + $group.name) }
        $actionDefinitions[$group.name] = $file.Name
    }
    foreach ($reference in $document.SelectNodes('//include_interrupt_actions')) {
        if ($reference.ref.StartsWith('MWEX_')) {
            $actionReferences += [pscustomobject]@{file=$file.Name;reference=$reference.ref}
        }
    }
}
foreach ($reference in $actionReferences) {
    if (-not $actionDefinitions.ContainsKey($reference.reference)) { throw ('Missing action library: ' + $reference.reference) }
}
$report = [pscustomobject]@{schema_validation=$results;base_patch_selectors=$selectors;action_libraries=$actionDefinitions;action_references=$actionReferences}
$report | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'schema_checks.json') -Encoding UTF8
if (@($results | Where-Object {$_.errors.Count -gt 0}).Count -gt 0) { throw 'Schema validation failed; see tools/schema_checks.json' }
if (@($selectors | Where-Object {$_.matches -ne 1}).Count -gt 0) { throw 'Patch selector validation failed; see tools/schema_checks.json' }
Write-Output ('Validated {0} XML files, {1} native patch selectors and {2} action libraries ({3} resolved references).' -f $results.Count,$selectors.Count,$actionDefinitions.Count,$actionReferences.Count)

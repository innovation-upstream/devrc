package main

// buildVersion is THE version: `default.nix` reads exactly this line
// (versionPattern `var buildVersion = "([^"]+)"`, exactly one match) and
// stamps the same value back with -X main.buildVersion. Write a literal
// here and both the store path and --version describe the code that was
// compiled — never a guess and never two truths.
//
// ⚠ The line's SHAPE is load-bearing: keep it one line, this spelling, and
// `var buildVersion = "…"` at the start of the statement.
var buildVersion = "0.1.0"

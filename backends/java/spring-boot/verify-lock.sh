#!/bin/sh
set -eu
mvn_cmd=${MAVEN_CMD:-mvn}
tmp=$(mktemp)
trap 'rm -f "$tmp" "$tmp.normal"' EXIT
"$mvn_cmd" -q dependency:list -DincludeScope=runtime -DoutputFile="$tmp" -DappendOutput=false
sed -n 's/^   \(.*:jar:.*\) -- module .*/\1/p' "$tmp" | LC_ALL=C sort > "$tmp.normal"
diff -u dependencies.lock "$tmp.normal"

#!/usr/bin/env bash

set -eu

NS="$1"
FIND_NAME="$2"
POD=$(kubectl get pods -n $NS | grep $FIND_NAME | awk '{print $1}' | head -n 1)

if [ -z "$POD" ]; then
  echo "Pod not found: $FIND_NAME in namespace $NS"
  exit 1
fi

FILE=$3
if [ -z "$FILE" ]; then
  echo "File not found"
  exit 1
fi

echo "Uploading $FILE to $POD in namespace $NS"
time kubectl -n $NS cp $FILE $POD:/tmp/archive.tar.gz
echo "Extracting $FILE to $POD in namespace $NS"
time kubectl -n $NS exec $POD -- bash -c "cd / && tar -xvf /tmp/archive.tar.gz"



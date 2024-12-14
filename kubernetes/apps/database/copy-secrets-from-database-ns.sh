#!/usr/bin/env bash
[ -z "$1" ] || [ -z "$2" ] && echo "Usage: $0 <namespace> <secret-name>" && exit 1

kubectl get secret -n "database" "postgres-pguser-$2" -o yaml \
  | sed "s/namespace: database/namespace: $1/" \
  | yq eval 'del(.metadata.ownerReferences)' - \
  | kubectl apply -f -

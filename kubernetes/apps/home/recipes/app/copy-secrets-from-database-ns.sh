#!/usr/bin/env bash
kubectl get secret postgres-pguser-recipes -n database -o yaml \
  | sed 's/namespace: database/namespace: home/' \
  | yq eval 'del(.metadata.ownerReferences)' - \
  | kubectl apply -f -

#!/usr/bin/env bash
# Read-only snapshot of everything that is NOT the lab: used before and after each lab deployment
# to prove the other project was not touched (criterion 1.17).
#
#   scripts/server/inventory.sh > before.txt   ... deploy ...   scripts/server/inventory.sh > after.txt
#   diff before.txt after.txt                  # must be empty
set -euo pipefail
echo "# namespaces (excluding lab)"
kubectl get namespaces -o name | grep -v -E '^namespace/ai-lab(-staging)?$'
echo "# resources in default"
kubectl -n default get all,pvc,configmap,secret,ingress,networkpolicy,serviceaccount -o name | grep -v '^pod/\|^job.batch/\|^replicaset' | sort
echo "# workload specs in default (generation = changes to the spec)"
kubectl -n default get deploy,statefulset,cronjob -o jsonpath='{range .items[*]}{.kind}/{.metadata.name} gen={.metadata.generation} images={.spec.template.spec.containers[*].image}{.spec.jobTemplate.spec.template.spec.containers[*].image}{"\n"}{end}' | sort
echo "# node ports in use"
kubectl get svc -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name} {.spec.ports[*].nodePort}{"\n"}{end}' | grep -v '^ai-lab' | sort
echo "# cluster-scoped storage (excluding lab)"
kubectl get pv,storageclass -o name | grep -v 'ai-lab' | sort
echo "# podman images (excluding lab)"
podman images --format '{{.Repository}}:{{.Tag}} {{.ID}}' | grep -v 'localhost/ai-lab-' | sort

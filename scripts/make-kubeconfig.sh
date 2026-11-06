#!/bin/sh
# Give the troubleshooter its own login to one cluster, so it needs no personal
# session to expire. Run once per cluster, where kubectl is already logged in
# as an admin of that cluster (kubectl config use-context <context> first).
#
#   scripts/make-kubeconfig.sh <cluster-name> [--look-only]
#
# Writes kube/<cluster-name>.yaml (mode 600). The token is never printed.
# To cut access: kubectl delete namespace troubleshooter
set -eu
cd "$(dirname "$0")/.."
name=${1:?Usage: scripts/make-kubeconfig.sh <cluster-name> [--look-only]}
mode=${2:-}
out="kube/$name.yaml"

kubectl apply -f k8s/look.yaml
if [ "$mode" = "--look-only" ]; then
  kubectl delete clusterrolebinding troubleshooter-fix --ignore-not-found
else
  kubectl apply -f k8s/fix.yaml
fi

n=0
until token=$(kubectl -n troubleshooter get secret troubleshooter-token -o 'jsonpath={.data.token}') && [ -n "$token" ]; do
  n=$((n + 1)); [ "$n" -lt 15 ] || { echo "No token after 15 seconds."; exit 1; }
  sleep 1
done
server=$(kubectl config view --minify -o 'jsonpath={.clusters[0].cluster.server}')
ca=$(kubectl -n troubleshooter get secret troubleshooter-token -o 'jsonpath={.data.ca\.crt}')

umask 077
cat > "$out" <<EOF
apiVersion: v1
kind: Config
clusters:
- name: $name
  cluster:
    server: $server
    certificate-authority-data: $ca
users:
- name: troubleshooter
  user:
    token: $(echo "$token" | base64 -d)
contexts:
- name: $name
  context:
    cluster: $name
    user: troubleshooter
current-context: $name
EOF
echo "Wrote $out. Now run ./ts check"

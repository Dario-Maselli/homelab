# read secret data at kv v2
path "kv/data/homelab/*" {
  capabilities = ["read"]
}

# list metadata for convenience
path "kv/metadata/homelab/*" {
  capabilities = ["list","read"]
}

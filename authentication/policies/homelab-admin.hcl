# Full control over homelab secrets in KV v2
path "kv/data/homelab/*" {
  capabilities = ["create","read","update","delete","list"]
}
path "kv/metadata/homelab/*" {
  capabilities = ["read","list","delete","update"]
}
# versioned operations (soft delete, undelete, destroy)
path "kv/delete/homelab/*"   { capabilities = ["update"] }
path "kv/undelete/homelab/*" { capabilities = ["update"] }
path "kv/destroy/homelab/*"  { capabilities = ["update"] }

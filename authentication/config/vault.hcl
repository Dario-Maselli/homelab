ui = true
disable_mlock = false

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = 1
  # when you add TLS later… set tls_disable = 0 and provide:
  # tls_cert_file = "/vault/config/tls/server.crt"
  # tls_key_file  = "/vault/config/tls/server.key"
}

storage "raft" {
  path    = "/vault/file"
  node_id = "vault-raft-1"
}

api_addr     = "http://127.0.0.1:8200"
cluster_addr = "http://127.0.0.1:8201"

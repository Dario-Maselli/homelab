# Vault for Homelab Authentication

This sets up a **HashiCorp Vault** service with integrated **Raft storage** inside the `homelab` repo under `authentication/`.  
It also includes a **policy for homelab-watcher** so the watcher can securely fetch secrets.

---

## 📂 Folder Structure
```
./authentication/
├── docker-compose.yaml
├── config/
│ └── vault.hcl
├── policies/
│ └── watcher.hcl
└── your-custom-directory/
  └── watcher.hcl
```

---

## 🚀 Running Vault

1. Start Vault with Docker Compose:

   ```bash
   docker compose up -d
   ```

2. Initialize Vault (this creates unseal keys and root token):

    ```bash
    docker exec -it vault vault operator init -key-shares=1 -key-threshold=1
    ```

    Save the Unseal Key and Initial Root Token securely.

3. Unseal Vault:

    ```bash
    docker exec -it vault vault operator unseal <Unseal-Key>
    ```

3. Login as root:

    ```bash
    docker exec -it vault vault login <Root-Token>

---

## ⚙️ Vault Configuration
`./config/vault.hcl`
```hcl
ui = true
disable_mlock = false

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = 1
}

storage "raft" {
  path    = "/vault/file"
  node_id = "vault-raft-1"
}

api_addr     = "http://127.0.0.1:8200"
cluster_addr = "http://127.0.0.1:8201"
```
`./config/vault.hcl`
```hcl
# allow watcher to read kv secrets
path "kv/data/homelab/*" {
  capabilities = ["read"]
}

# allow listing secrets under homelab namespace
path "kv/metadata/homelab/*" {
  capabilities = ["list", "read"]
}
```

---

## 🔑 Enabling KV Secrets Engine

1. Enable the KV v2 engine:

   ```bash
   docker exec -e VAULT_ADDR=http://127.0.0.1:8200 -it vault vault secrets enable -path=kv kv-v2
   ```

2. Store a test secret:

    ```bash
    docker exec -e VAULT_ADDR=http://127.0.0.1:8200 -it vault vault kv put kv/homelab/watcher DB_USER=test DB_PASS=secret
    ```

---

## 👤 Create Watcher Policy & Token

1. Write the policy:

   ```bash
   docker exec -e VAULT_ADDR=http://127.0.0.1:8200 -it vault vault policy write homelab-watcher /vault/policies/watcher.hcl
   ```

2. Create a token for watcher:

    ```bash
    docker exec -e VAULT_ADDR=http://127.0.0.1:8200 -it vault vault token create -policy="homelab-watcher"
    ```

    Save this token. The watcher will use it for authentication.

---

## 🔗 Using in homelab-watcher

In homelab-watcher, configure your environment:

```bash
docker exec -e VAULT_ADDR=http://127.0.0.1:8200 -it vault vault token create -policy="homelab-watcher" 
```

Your watcher can then fetch secrets like:

```python
import hvac

client = hvac.Client(url="http://vault:8200", token="WATCHER_TOKEN")
secrets = client.secrets.kv.v2.read_secret_version(path="homelab/watcher")
print(secrets["data"]["data"])
```

---

## ✅ Summary

- docker compose up -d → starts Vault

- vault operator init / unseal / login → initializes

- vault secrets enable kv-v2 → creates a KV store

- vault policy write homelab-watcher → defines watcher policy

- vault token create → gives watcher its access token

- homelab-watcher uses Vault API to fetch secrets at runtime

---

## 📚 References

- [Vault Docs](https://developer.hashicorp.com/vault/docs)


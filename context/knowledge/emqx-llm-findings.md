## 2026-04-12 | phase: messaging | sub_goal: emqx
### Observations
- [pod] All 3 EMQX pods (emqx-0, emqx-1, emqx-2) are in Running and Ready (1/1) state, indicating the process is starting normally.
- [config] Current configuration is consistent across all pods: EMQX_CLUSTER__DNS__RECORD_TYPE is 'a' and EMQX_NODE__NAME is 'emqx@$(POD_IP)' (e.g., emqx@10.244.2.247). This matches the current Helm values in emqx-dev-v1.
- [logs] Pod logs show 'Stopping mria, reason: join' but no subsequent logs indicating successful peer discovery or cluster joining, resulting in only 1 node being active (running_nodes=1).
- [network] Connectivity to the headless service (emqx-headless.gikview.svc.alpha.nexus.local:4370) is functional (verified via curl), but the cluster fails to form, suggesting that DNS resolution for the headless service may not be returning all pod IPs or the 'a' record discovery strategy is not effectively finding peers in this environment.
- [previous_attempts] Previous attempts to use 'srv' records with FQDN node names also failed to form a cluster, and using 'a' records with FQDN node names caused 'integrity_validation_failure'. The current 'a' records with IP-based node names are also failing to cluster.
### Suggestions
- In `edge-server/helm/emqx/values-dev.yaml`, change `emqxConfig.EMQX_CLUSTER__DNS__RECORD_TYPE: "a"` back to `emqxConfig.EMQX_CLUSTER__DNS__RECORD_TYPE: "srv"` and change `emqxConfig.EMQX_NODE__NAME: "emqx@$(POD_IP)"` back to `emqxConfig.EMQX_NODE__NAME: "emqx@$(POD_NAME).emqx-headless.gikview.svc.alpha.nexus.local"` to align with the Technology Knowledge. Since 'a' records also failed, the FQDN/SRV approach is the intended standard for this service.
---
## 2026-04-12 | phase: messaging | sub_goal: emqx
### Observations
- [cluster] EMQX cluster failed to form (running_nodes=1), despite all 3 pods being in Running state.
- [config] The deployment uses 'srv' record type for DNS discovery (EMQX_CLUSTER__DNS__RECORD_TYPE: 'srv') and FQDN-based node names, which is the correct configuration for EMQX 5.8.6.
- [network] The headless service 'emqx-headless' defines the clustering port (4370) with the name 'ekka'. In Kubernetes, this creates an SRV record named '_ekka._tcp.emqx-headless.gikview.svc.alpha.nexus.local'.
- [discovery] EMQX 5.x DNS discovery strategy expects the SRV record to be named '_emqx._tcp.<dns_name>' by default. Because the port is named 'ekka' instead of 'emqx', the pods cannot discover each other via SRV records, preventing cluster formation.
### Suggestions
- In `edge-server/helm/emqx/templates/service-headless.yaml`, change the port name `ekka` to `emqx` for port 4370 to ensure the correct SRV record (`_emqx._tcp...`) is created by Kubernetes.
---
## 2026-04-12 | phase: messaging | sub_goal: emqx
### Observations
- [pod] All 3 EMQX pods are in Running state, but they are experiencing frequent restarts. Events show readiness and liveness probes failing with 'context deadline exceeded', indicating the EMQX process is unresponsive or under heavy load.
- [logs] EMQX logs show that nodes are successfully discovering each other via DNS (e.g., 'cm_registry_mnesia_down' for peers), but they are unable to maintain the cluster connection, leading to a flapping state where nodes join and then immediately drop.
- [config] The current configuration uses EMQX_CLUSTER__DNS__RECORD_TYPE: 'srv', FQDN-based EMQX_NODE__NAME, and the headless service port 4370 is correctly named 'emqx'. This aligns with Technology Knowledge and is verified as the correct setup for EMQX 5.8.6.
- [network] Connectivity to the headless service and individual pod FQDNs on port 4370 is functional, confirming that DNS resolution and network paths are correct.
### Suggestions
- In `edge-server/helm/emqx/values-dev.yaml`, increase `resources.limits.memory` from `512Mi` to `1Gi` and `resources.requests.memory` from `384Mi` to `512Mi` to prevent process hangs and probe timeouts that are causing pod restarts and cluster instability.
---

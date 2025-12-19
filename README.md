# Deploy an Open Source LLM on Kubernetes with KubeRay 

# Overview
This project shows how to run an open-source Large Language Model (LLM), specifically the Qwen3-Coder-30B-A3B-Instruct, on enterprise-grade infrastructure. In this case, we will use it for a coding assistant scenario.
This setup provides a complete end-to-end pipeline: from provisioning GPU-backed Kubernetes clusters to serving the model via a secure API and connecting it to real-world developer tools like OpenCode and OpenWebUI.

# Core Components
* Model: The [Qwen3-Coder-30B-A3B-Instruct](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct), a model optimized for coding tasks that balances high capabilities with lightweight resource requirements.
* Orchestration ([KubeRay](https://github.com/ray-project/kuberay)): A Kubernetes operator that manages Ray Clusters and Ray Services, providing the framework needed to schedule, deploy, and serve LLMs at scale.
* Infrastructure ([Akamai Cloud](https://www.linode.com/lp/free-credit-100-5000/?promo=sitelin100-02162023&promo_value=100&promo_length=60&utm_source=google&utm_medium=cpc&utm_campaign=f-mc-65659&utm_id=cloud&utm_content=US-EN_NB_CL_PLG_VPS&utm_placement=NORAM&gad_source=1&gad_campaignid=1706209438&gbraid=0AAAAAD_kTnU8v3h7Z9I9UhxMOpiO_mFIU&gclid=Cj0KCQiAjJTKBhCjARIsAIMC449C-p-2UCh4rRKd7tkhv4gmY-NyWx70gTmEwtlrZO4Z0HlFWgWCd3IaAo9CEALw_wcB)): High-performance NVIDIA Blackwell or Ada GPU nodes on Linode Kubernetes Engine (LKE).
* Networking (Istio & Gateway API): Modern ingress management using Istio and the Kubernetes Gateway API to route and secure traffic to the LLM service.
* Clients: Integration with OpenCode for AI-powered IDE features and OpenWebUI for a familiar, browser-based chat interface.

# The "Why": Scalable, Private AI

## Why Ray and KubeRay?
Ray is an open-source unified compute framework that simplifies the process of scaling Python and AI workloads. Running LLMs often requires distributed computing across multiple GPUs; Ray handles this complexity (such as tensor parallelism) natively.

KubeRay brings this power to Kubernetes. By using the KubeRay operator, you can manage your AI infrastructure as code, allowing for:

* Simplified Deployment: Using custom resources like RayService to define the model's environment and scaling logic in a single YAML file.
* Elastic Scaling: Automatically adjusting the number of worker replicas based on request traffic to optimize resource usage.
* Resiliency: Leveraging Kubernetes' self-healing capabilities to ensure your LLM endpoints remain highly available.

# Prerequisites

1. A [HuggingFace](https://huggingface.co/) account, to download LLM weights.
2. An Akamai Cloud (formerly Linode) account with access to GPUs. 
For NVIDIA RTX 4000 Ada GPUs, see [here](https://techdocs.akamai.com/cloud-computing/docs/gpu-compute-instances).
For NVIDIA Blackwell, request access [here](https://cloud.linode.com/support/tickets?dialogOpen=true). 
3. [Opencode](https://opencode.ai/) installed on your device. 
4. Helm and kubectl installed on your device.
5. [Linode CLI](https://github.com/linode/linode-cli) installed on your device.

ALL code samples are available at [https://github.com/akamai-developers/kuberay-gpu-llm-quickstart](https://github.com/akamai-developers/kuberay-gpu-llm-quickstart) 

# Deployment Steps

## 1. Clone this repository

```sh
git clone <repository-url> kuberay-gpu-llm-quickstart
cd kuberay-gpu-llm-quickstart
```

## 2. Create a Linode API key

In the Akamai Cloud Console, create a Linode API key with read/write permissions for Kubernetes, and NodeBalancers, and read permissions for events.  
![Linode PAT Creation](screencasts/01-LinodePAT.gif)

## 3. Create a LKE Cluster with an NVIDIA Blackwell GPU 

Create a LKE Cluster with two node pools \- one with a blackwell-gpu, and one with a standard linode type, to run our other workloads on

```sh
export LINODE_CLI_TOKEN=<token from previous step>

linode-cli lke cluster-create \
--k8s_version 1.34 \
  --label myllm \
  --region us-sea \
  --tier standard \
  --control_plane.high_availability true \
  --node_pools.count 1 \
  --node_pools.type g3-gpu-rtxpro6000-blackwell-2 \
  --node_pools.count 3 \
  --node_pools.type g6-standard-4 \
  --json | jq -r '.[].id'
export CLUSTER_ID=<id from the previous command>
```

If you are using Ada GPUs, replace `3-gpu-rtxpro6000-blackwell-2` with `g2-gpu-rtx4000a4-m` in the command above.

![LKE Cluster Creation](screencasts/02-cluster-create.gif)

## 4. Fetch Kubeconfig, install NVIDIA Operator 

Wait for the cluster’s kubeconfig to be ready, and save it.

```sh
linode-cli lke kubeconfig-view $CLUSTER_ID --json  | jq  -r '.[].kubeconfig' | base64 -d > kubeconfig.yaml 
```

Then Install nvidia gpu operator. This provisions and configures the GPU drivers, and the Nvidia device plugin. This step allows us to use the GPU within Kubernetes.

```sh
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia \
helm repo update
helm install --wait --generate-name \
    -n gpu-operator --create-namespace \
    nvidia/gpu-operator \
    --version=v25.10.0
```

![Nvidia Operator Install](screencasts/03-nvidia-operator.gif)

## 5. Install KubeRay using helm

```sh
helm repo add kuberay https://ray-project.github.io/kuberay-helm/
helm repo update
# Install both CRDs and KubeRay operator v1.5.0.
helm install --wait kuberay-operator kuberay/kuberay-operator --version 1.5.0
```

Verify that the operator is running ok

```sh
kubectl get po
NAME                                READY   STATUS    RESTARTS   AGE
kuberay-operator-5c575cccb6-b99wj   1/1     Running   0          12m
```

![KubeRay installation](screencasts/04-kuberay-install.gif)

## 6. Install gateway API CRDs & Istio

Gateway API is the modern way of doing ingress to services in a Kubernetes cluster. The CRDs give us the primitives we need  
 

```sh
kubectl get crd gateways.gateway.networking.k8s.io &> /dev/null || \
{ kubectl kustomize "github.com/kubernetes-sigs/gateway-api/config/crd?ref=v1.4.0" | kubectl apply -f -; }
```

Install Istio using helm. Istio is going to be the “Gateway Controller” which takes the `Gateway` custom resources and creates a linode `NodeBalancer` to route traffic from outside the cluster to the LLM running inside the cluster

```
helm repo add istio https://istio-release.storage.googleapis.com/charts
helm repo update

helm install --wait istio-base istio/base -n istio-system --set defaultRevision=default --create-namespace
helm install --wait istiod istio/istiod -n istio-system
```

![Istio Gateway Install](screencasts/05-istio-gateway-install.gif)

## 7. Create a HuggingFace APIToken

HuggingFace allows you to download models from multiple providers \- it is similar to dockerHub, but for Machine Learning Models. Create an API key to download models

![HuggingFace Token creation](screencasts/06-hf-token-create.gif)

Once you have the hugging face token, set it in your environment. Also generate a random key \- this key will be used to secure traffic to the LLM we deploy. Choose this at random, and export it into the environment. 

```sh
echo "export HF_TOKEN=<yourtokenhere>" > .envrc
echo "export OPEN_API_KEY=<your chosen key here>" > .envrc
```

## 8. Deploy 🚀

Now we’re all set to deploy things to the cluster\! Deploy the ray-serve config \- this deploys the model qwen-coder-30b.  Qwen is a family of models developed by Alibaba. The 30b coder model is optimized for coding tasks, but isn’t super huge that you need a GPU farm to run. It strikes a balance between capabilities and being lightweight. 

This step will take some time, so this will be a great time to grab a cup of your favorite beverage 😀

```sh
source .envrc
export KUBECONFIG=kubeconfig.yaml
kustomize build manifests | envsubst | kubectl apply -f -
```

Wait for it to be healthy - check status for the model

```sh
kubectl describe rayservice ray-serve-llm
```

![Deploy Ray Service](screencasts/07-apply-rayservice.gif)

## 9. Inspect model using the dashboard

KubeRay allows us to view the status and inspect the state of the cluster \- the admin interface can be accessed using a port-forward

```sh
kubectl port-forward svc/ray-serve-llm-head-svc 8265
```

![Ray Admin Dashboard](screencasts/08-dashboard.gif)

## 10. Testing time 🧪

Test your model by sending some test messages

```sh
SERVICE_IP=$(kubectl get svc llm-gateway-istio -o yaml | yq -r '.status.loadBalancer.ingress[0].ip')

curl --location "http://$SERVICE_IP/v1/chat/completions" --header "Authorization: Bearer $OPEN_API_KEY" --header "Content-Type: application/json" --data '{
      "model": "qwen3-coder-30b-a3b-instruct",
      "messages": [
          {
              "role": "system", 
              "content": "You are a helpful assistant."
          },
          {
              "role": "user", 
              "content": "Provide steps to configure Ray on LKE"
          }
      ]
  }'
```

![Test Model](screencasts/09-test-model.gif)

## 11. Configure OpenCode

```sh
SERVICE_IP=$(kubectl get svc llm-gateway-istio -o yaml | yq -r '.status.loadBalancer.ingress[0].ip')
```
```sh
{
    "$schema": "https://opencode.ai/config.json",
    "provider": {
        "mymodel": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "My awesome model",
            "options": {
                "baseURL": "http://$SERVICE_IP/v1"
            },
            "models": {
                "qwen3-coder-30b-a3b-instruct": {
                    "name": "Qwen3 Coder"
                }
            }
        }
    }
}
```

And then login using 

```sh
opencode auth login
```

And scroll all the way to the bottom and choose other, enter your API key

![OpenCode configuration](screencasts/10-opencode.gif)

## 12. Configure OpenWebUI (Optional)

Sometimes, it's nice to open-up the browser and just chat with the LLM, instead of firing up opencode. Let's see how to do that\!

```sh
cat <<EOF >openwebui-values.yaml
ollama:
  enabled: false
openaiBaseApiUrls:
  - "http://ray-serve-llm-serve-svc.default.svc.cluster.local:8000/v1"
openaiApiKeys:
  - "$OPEN_API_KEY"

service:
  type: ClusterIP #change this to Loadbalancer to expose this publicly.
  port: 8080
EOF
```
```sh
helm install open-webui open-webui/open-webui \
  --namespace open-webui \
  --create-namespace \
  -f openwebui-values.yaml
```

![Installing OpenWebUI](screencasts/11-openwebui-install.gif)

Chat away!

![Using OpenWebUI](screencasts/12-openwebui-usage.gif)

## Why Akamai Cloud GPUs?
Traditional AI API providers often come with unpredictable costs and data privacy concerns. Deploying on Akamai Cloud (formerly Linode) solves both:
* Performance: Akamai's Blackwell and Ada GPUs provide the high-memory bandwidth and throughput necessary to run complex 30B+ parameter models locally.
* Predictable Economics: You pay only for the hardware you use, eliminating the "token-based" pricing models of black-box AI services.
* Intellectual Property Protection: Because you control the entire stack, you can rest assured that your proprietary code and data are never used for training third-party models.


#!/usr/bin/env bash
# 하네스 외부 CLI 툴 설치 스크립트
# 버전 명세: config/tools.yaml
set -euo pipefail

INSTALL_DIR="${HOME}/.local/bin"
mkdir -p "${INSTALL_DIR}"

KUBECONFORM_VERSION="0.7.0"
TRIVY_VERSION="0.69.3"
GITLEAKS_VERSION="8.24.3"

ARCH="$(uname -m)"
case "${ARCH}" in
  x86_64)  ARCH_KUBECONFORM="amd64"; ARCH_TRIVY="64bit"; ARCH_GITLEAKS="x64" ;;
  aarch64) ARCH_KUBECONFORM="arm64"; ARCH_TRIVY="ARM64"; ARCH_GITLEAKS="arm64" ;;
  *) echo "Unsupported arch: ${ARCH}"; exit 1 ;;
esac

echo "==> kubeconform v${KUBECONFORM_VERSION}"
curl -sSL "https://github.com/yannh/kubeconform/releases/download/v${KUBECONFORM_VERSION}/kubeconform-linux-${ARCH_KUBECONFORM}.tar.gz" \
  | tar -xz -C "${INSTALL_DIR}" kubeconform
chmod +x "${INSTALL_DIR}/kubeconform"

echo "==> trivy v${TRIVY_VERSION}"
curl -sSL "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-${ARCH_TRIVY}.tar.gz" \
  | tar -xz -C "${INSTALL_DIR}" trivy
chmod +x "${INSTALL_DIR}/trivy"

echo "==> gitleaks v${GITLEAKS_VERSION}"
curl -sSL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_${ARCH_GITLEAKS}.tar.gz" \
  | tar -xz -C "${INSTALL_DIR}" gitleaks
chmod +x "${INSTALL_DIR}/gitleaks"

# kubectl, helm은 패키지매니저 또는 공식 문서로 설치
# kubectl: https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/
# helm:    https://helm.sh/docs/intro/install/

echo ""
echo "==> 설치 완료. 버전 확인:"
"${INSTALL_DIR}/kubeconform" -v
"${INSTALL_DIR}/trivy" --version | head -1
"${INSTALL_DIR}/gitleaks" version

echo ""
echo "PATH에 ${INSTALL_DIR}가 없다면 ~/.bashrc 또는 ~/.zshrc에 추가하세요:"
echo "  export PATH=\"${INSTALL_DIR}:\$PATH\""

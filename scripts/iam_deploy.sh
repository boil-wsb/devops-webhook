#!/bin/bash
set -e

echo "==="
echo "IMAGE: ${PROJECTNAME}:${REF}"
echo "REPO_URL: ${REPO_URL} DEPLOY_DIR: ${DEPLOY_DIR}"

echo "docker pull ${REPO_URL}${PROJECTNAME}:${REF} && sleep 5 && docker compose -f ${DEPLOY_DIR}/docker-compose.yaml up -d ${PROJECTNAME}"
docker pull ${REPO_URL}${PROJECTNAME}:${REF}
sleep 5 
docker compose -f ${DEPLOY_DIR}/docker-compose.yaml up -d ${PROJECTNAME}

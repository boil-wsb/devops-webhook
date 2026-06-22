#!/bin/bash
set -e

echo "==="
echo "PROJECTNAME: ${PROJECTNAME}  REF: ${REF}"
echo "PROJECT: ${PROJECT}"
echo "MINIO_ENDPOINT: ${MINIO_ENDPOINT}  NEXUS_URL: ${NEXUS_URL}"

# 登录 Nexus Docker 镜像仓库
docker login ${NEXUS_URL} -u ${NEXUS_USER} -p ${NEXUS_PASSWORD}

# 拉取 Docker 镜像
echo "docker pull ${NEXUS_URL}/${PROJECTNAME}:${REF}"
docker pull ${NEXUS_URL}/${PROJECTNAME}:${REF}

# 保存 Docker 镜像为 tar
IMAGE_TAR="${PROJECTNAME}_${REF}.tar"
docker save -o ${IMAGE_TAR} ${NEXUS_URL}/${PROJECTNAME}:${REF}

# 配置 mc 别名
mc alias set minio http://${MINIO_ENDPOINT} ${MINIO_ACCESS_KEY} ${MINIO_SECRET_KEY} --api s3v4

# TODO: 从 MinIO 下载所需文件
# mc cp minio/BUCKET/PATH/FILE ./FILE

# TODO: 组装统一安装包
# tar czf ${PROJECTNAME}_${REF}_install.tar.gz ${IMAGE_TAR} ./FILE ...

# TODO: 上传安装包到 MinIO
# mc cp ${PROJECTNAME}_${REF}_install.tar.gz minio/BUCKET/PATH/

# 清理临时文件
rm -f ${IMAGE_TAR}

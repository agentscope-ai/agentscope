#!/usr/bin/env bash
set -euo pipefail
#bash compose-images.sh  -f ../docker-compose.uat.yml   -o -d ./ 
bash compose-images.sh  -f ./docker-compose.yml   -i -d ./ 
# compose-images.sh
# 功能：按照指定 docker-compose 文件的 image 列表，支持：
#   -o 保存镜像到 -d 指定目录（默认当前目录）
#   -i 从 -d 指定目录加载 *.tar 为镜像，并校验 compose 中的镜像是否存在
#   -f 指定 docker-compose 文件（可重复叠加，如 -f base.yml -f override.yml）
#   -d 指定目录（默认 .）
#   -s 指定 service 过滤（多个用逗号分隔）
# 用法示例：
#   保存（单文件，自动跳过无 image 的服务）：bash ./compose-images.sh -o -f ./docker-compose.easyparse.yml -d ./
#   保存（多文件叠加，按 docker compose 语义合并）：bash ./compose-images.sh -o -f ./docker-compose.yml -f ./docker-compose.easyparse.yml -d ./
#   加载：./compose-images.sh -i -f ./docker-compose.yml -d ./images

OUT_DIR="."
COMPOSE_FILES=()   # 支持多个 compose 文件叠加
ACTION=""          # save 或 load
SERVICES=()        # -s 指定的 service 名过滤列表，为空时处理全部

usage() {
  cat <<EOF
用法: $0 [-o | -i] [-f COMPOSE_FILE]... [-d OUT_DIR] [-s SERVICES]
  -o    保存 compose 中的镜像到 -d 目录（默认当前目录）
  -i    从 -d 目录加载 *.tar 为镜像，并校验 compose 中列出的镜像是否已存在
  -f    指定 docker-compose 文件（可多次指定以叠加，如 -f base.yml -f override.yml）。默认 ./docker-compose.yml
  -d    指定目录（默认 .）
  -s    只处理指定的服务，多个用逗号分隔，例如 -s backend,nginx。不指定则处理全部服务

说明：
  - 脚本会跳过 compose 中既没有 image 字段也没有 build context 的服务（如 override 文件中的纯环境变量/挂载覆盖项）
  - 多个 -f 按 docker compose 语义合并（后面的覆盖前面的 services 字段）
示例:
  保存全部: $0 -o -f ./docker-compose.yml -d ./images
  加载全部: $0 -i -f ./docker-compose.yml -d ./images
  保存指定服务: $0 -o -f ./docker-compose.yml -d ./images -s backend,nginx
  加载指定服务校验: $0 -i -f ./docker-compose.yml -d ./images -s backend
  保存 base+override: $0 -o -f ./docker-compose.yml -f ./docker-compose.easyparse.yml -d ./
  仅 override（自动跳过 backend/nginx 等覆盖项）: $0 -o -f ./docker-compose.easyparse.yml -d ./
EOF
}

# 解析参数（手动解析以支持 -f 重复出现）
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) ACTION="save"; shift ;;
    -i) ACTION="load"; shift ;;
    -f)
      if [[ -z "${2:-}" ]]; then
        echo "选项 -f 需要参数" >&2
        usage
        exit 2
      fi
      COMPOSE_FILES+=("$2")
      shift 2
      ;;
    -d)
      if [[ -z "${2:-}" ]]; then
        echo "选项 -d 需要参数" >&2
        usage
        exit 2
      fi
      OUT_DIR="$2"
      shift 2
      ;;
    -s)
      if [[ -z "${2:-}" ]]; then
        echo "选项 -s 需要参数" >&2
        usage
        exit 2
      fi
      IFS=',' read -r -a SERVICES <<<"$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "未知选项: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$ACTION" ]]; then
  echo "错误：必须指定 -o（保存）或 -i（加载）中的一个" >&2
  usage
  exit 2
fi

if [[ ${#COMPOSE_FILES[@]} -eq 0 ]]; then
  COMPOSE_FILES=("./docker-compose.yml")
fi

# 校验所有 compose 文件存在并组装 docker compose 参数数组
COMPOSE_FILE_ARGS=()
for f in "${COMPOSE_FILES[@]}"; do
  if [[ ! -f "$f" ]]; then
    echo "错误：找不到 compose 文件: $f" >&2
    exit 1
  fi
  COMPOSE_FILE_ARGS+=("-f" "$f")
done

# 查找 docker compose 命令（优先 v2 的 `docker compose`）
find_compose_cmd() {
  if command -v docker &>/dev/null; then
    if docker compose version &>/dev/null; then
      echo "docker compose"
      return 0
    fi
  fi
  if command -v docker-compose &>/dev/null; then
    echo "docker-compose"
    return 0
  fi
  return 1
}

COMPOSE_CMD=$(find_compose_cmd) || { echo "错误：未找到 docker compose 或 docker-compose 命令" >&2; exit 1; }

# 检查 PyYAML 是否可用（用于解析 override 文件等场景）
HAS_PYYAML=0
if python3 -c "import yaml" 2>/dev/null; then
  HAS_PYYAML=1
fi

# 用 PyYAML 合并多个 compose 文件，按 docker compose 语义返回 services 字典
# （后者覆盖前者的 services 字段；同 service 内是深度合并，后者非 dict 字段覆盖前者）
merge_compose_services() {
  python3 - "${COMPOSE_FILES[@]}" <<'PYEOF'
import sys
import yaml

def deep_merge(base, override):
    """深度合并：override 中非 dict 字段覆盖 base；dict 字段递归合并"""
    for key, value in (override or {}).items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base

files = sys.argv[1:]
merged_services = {}
for fp in files:
    with open(fp, 'r', encoding='utf-8') as fh:
        try:
            data = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            print(f"WARN: 解析 {fp} 失败: {exc}", file=sys.stderr)
            continue
        deep_merge(merged_services, data.get('services') or {})

# 按 service 名排序输出，方便后续去重；用 | 作分隔符以避免 bash IFS 合并 tab 的坑
for name in sorted(merged_services.keys()):
    svc = merged_services.get(name) or {}
    image = svc.get('image') or ''
    has_build = 'build' in svc
    print(f"{name}|{image}|{int(has_build)}")
PYEOF
}

# 对仅有 build、没有 image 的服务，调用 docker compose config 拿到构建后的镜像名
# 这是 compose 的标准行为：build 会自动生成 <project>-<service> 形式的镜像
resolve_build_images() {
  local names=("$@")
  if [[ ${#names[@]} -eq 0 ]]; then
    return 0
  fi
  local cfg
  if ! cfg=$(${COMPOSE_CMD} "${COMPOSE_FILE_ARGS[@]}" config 2>/dev/null); then
    echo "WARN: docker compose config 失败，无法解析仅 build 服务的镜像名" >&2
    return 0
  fi
  for svc in "${names[@]}"; do
    [[ -z "$svc" ]] && continue
    # 提取该 service 的 image 行（docker compose config 已合并所有 override）
    local img
    img=$(echo "$cfg" | awk -v svc="$svc" '
      $0 ~ "^  " svc ":" { in_svc = 1; next }
      in_svc && /^  [a-zA-Z0-9_.-]+:/ { in_svc = 0 }
      in_svc && /^[[:space:]]+image:/ {
        sub(/^[[:space:]]+image:[[:space:]]*/, "")
        gsub(/["'\'']/, "")
        sub(/[[:space:]].*$/, "")
        print
        exit
      }
    ')
    if [[ -n "$img" ]]; then
      echo "BUILD|$svc|$img"
    fi
  done
}

# 提取镜像列表
# 输出格式：每行 "<svc>\t<image>" 或空（无任何有效 image）
get_images() {
  local target_services=()
  if [[ ${#SERVICES[@]} -gt 0 ]]; then
    target_services=("${SERVICES[@]}")
  fi

  local rows
  if [[ $HAS_PYYAML -eq 1 ]]; then
    rows=$(merge_compose_services)
  else
    echo "错误：未检测到 PyYAML（python3 -c 'import yaml'），无法安全解析 compose 文件" >&2
    return 1
  fi

  local skipped=()
  local build_only=()   # 只有 build 没有 image 的服务名
  declare -A img_of_svc

  while IFS='|' read -r svc image has_build; do
    [[ -z "$svc" ]] && continue
    # 应用 -s 过滤
    if [[ ${#target_services[@]} -gt 0 ]]; then
      local matched=0
      for s in "${target_services[@]}"; do
        [[ "$s" == "$svc" ]] && matched=1 && break
      done
      [[ $matched -eq 0 ]] && continue
    fi

    if [[ -n "$image" ]]; then
      img_of_svc["$svc"]="$image"
    elif [[ "$has_build" == "1" ]]; then
      build_only+=("$svc")
    else
      skipped+=("$svc")
    fi
  done <<<"$rows"

  # 处理只有 build 的服务：用 docker compose config 拿构建后的镜像名
  if [[ ${#build_only[@]} -gt 0 ]]; then
    while IFS='|' read -r kind svc img; do
      [[ "$kind" == "BUILD" && -n "$img" ]] && img_of_svc["$svc"]="$img"
    done <<<"$(resolve_build_images "${build_only[@]}")"
    # 如果 docker compose config 整体失败，build_only 全部跳过并警告
    for svc in "${build_only[@]}"; do
      [[ -z "${img_of_svc[$svc]:-}" ]] && skipped+=("$svc")
    done
  fi

  if [[ ${#skipped[@]} -gt 0 ]]; then
    echo "WARN: 以下服务既无 image 也无 build context，已跳过: ${skipped[*]}" >&2
  fi

  local img_count=0
  if [[ "${img_of_svc[@]+x}" == "x" ]]; then
    img_count="${#img_of_svc[@]}"
  fi
  if [[ "$img_count" -eq 0 ]]; then
    return 0
  fi

  # 按镜像名去重输出
  for svc in "${!img_of_svc[@]}"; do
    echo "${img_of_svc[$svc]}"
  done | sort -u
}

# 为镜像名生成安全文件名
safe_name() {
  echo -n "$1" | sed 's#[/:]#_#g'
}

save_images() {
  mkdir -p "$OUT_DIR"
  local imgs
  imgs=$(get_images)
  if [[ -z "$imgs" ]]; then
    echo "警告：未在 compose 中发现 image 字段。若服务通过 build 生成镜像且未命名 image，请先构建并手动保存对应镜像名。" >&2
  fi
  echo "Compose 文件: ${COMPOSE_FILES[*]}"
  echo "输出目录: $OUT_DIR"
  if [[ ${#SERVICES[@]} -gt 0 ]]; then
    echo "指定服务: ${SERVICES[*]}"
  fi
  echo "准备保存以下镜像："
  echo "$imgs" | sed 's/^/  - /'

  while IFS= read -r image; do
    [[ -z "$image" ]] && continue
    local fname
    fname=$(safe_name "$image")
    local out_file="$OUT_DIR/${fname}.tar"

    echo "==> 处理镜像: $image"
    if [[ -f "$out_file" ]]; then
      echo "   已存在，跳过: $out_file"
      continue
    fi
    if ! docker image inspect "$image" >/dev/null 2>&1; then
      if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "   [DRY_RUN] 本地未找到镜像 $image，跳过 docker pull"
      else
        echo "   本地未找到镜像，尝试 docker pull $image ..."
        docker pull "$image"
      fi
    fi
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
      echo "   [DRY_RUN] skip docker save -o $out_file $image"
    else
      echo "   保存为: $out_file"
      docker save -o "$out_file" "$image"
    fi
  done <<<"$imgs"
  echo "保存完成，输出目录: $OUT_DIR"
}

load_images() {
  if [[ ! -d "$OUT_DIR" ]]; then
    echo "错误：目录不存在: $OUT_DIR" >&2
    exit 1
  fi
  echo "Compose 文件: ${COMPOSE_FILES[*]}"
  echo "从目录加载 tar：$OUT_DIR"
  if [[ ${#SERVICES[@]} -gt 0 ]]; then
    echo "指定服务: ${SERVICES[*]}（仅加载对应服务的 tar，校验也只针对这些服务）"
  fi

  # 构建待加载 tar 文件集：未指定服务时为目录下全部 *.tar；指定后只加载对应镜像的 tar
  local tar_files=()
  if [[ ${#SERVICES[@]} -eq 0 ]]; then
    shopt -s nullglob
    tar_files=("$OUT_DIR"/*.tar)
    shopt -u nullglob
  else
    local target_imgs
    target_imgs=$(get_images)
    while IFS= read -r image; do
      [[ -z "$image" ]] && continue
      local fname
      fname=$(safe_name "$image")
      local tarf="$OUT_DIR/${fname}.tar"
      if [[ -f "$tarf" ]]; then
        tar_files+=("$tarf")
      else
        echo "警告：未找到服务对应的 tar：$tarf（镜像：$image）" >&2
      fi
    done <<<"$target_imgs"
  fi

  if [[ ${#tar_files[@]} -eq 0 ]]; then
    echo "警告：目录中未找到任何可加载的 *.tar 文件" >&2
  fi

  for tarf in "${tar_files[@]}"; do
    echo "==> docker load -i $tarf"
    docker load -i "$tarf"
  done

  # 加载后校验 compose 中的镜像是否存在
  local imgs
  imgs=$(get_images)
  local missing=()
  while IFS= read -r image; do
    [[ -z "$image" ]] && continue
    if ! docker image inspect "$image" >/dev/null 2>&1; then
      missing+=("$image")
    fi
  done <<<"$imgs"

  if [[ ${#missing[@]} -gt 0 ]]; then
    echo -e "\n以下镜像在加载后仍未找到（可能 tar 中不含该镜像或标签不匹配）："
    for m in "${missing[@]}"; do
      echo "  - $m"
    done
    echo "提示：请确保保存时使用的镜像名与 compose 中一致的 name:tag，或重新保存/加载对应 tar。"
  else
    echo "已成功加载并存在 compose 中声明的所有镜像。"
  fi
}

case "$ACTION" in
  save) save_images ;;
  load) load_images ;;
  *) echo "未知动作: $ACTION" >&2; usage; exit 2 ;;
esac

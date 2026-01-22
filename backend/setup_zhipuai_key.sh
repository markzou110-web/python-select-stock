#!/bin/bash
# 智谱AI API Key 配置助手
# 用法: bash setup_zhipuai_key.sh

echo "=================================================="
echo "  智谱AI API Key 配置助手"
echo "=================================================="
echo ""

# 检测当前使用的 shell
CURRENT_SHELL=$(echo $SHELL)
echo "检测到当前 shell: $CURRENT_SHELL"

if [[ "$CURRENT_SHELL" == *"zsh"* ]]; then
    CONFIG_FILE="$HOME/.zshrc"
    echo "将配置文件: ~/.zshrc"
elif [[ "$CURRENT_SHELL" == *"bash"* ]]; then
    CONFIG_FILE="$HOME/.bash_profile"
    echo "将配置文件: ~/.bash_profile"
else
    CONFIG_FILE="$HOME/.profile"
    echo "将配置文件: ~/.profile"
fi

echo ""
echo "请输入你的智谱AI API Key:"
read -r API_KEY

if [ -z "$API_KEY" ]; then
    echo "❌ API Key不能为空"
    exit 1
fi

echo ""
echo "正在添加到 $CONFIG_FILE ..."

# 检查是否已经存在
if grep -q "ZHIPUAI_API_KEY" "$CONFIG_FILE" 2>/dev/null; then
    echo "⚠️  发现已存在的 ZHIPUAI_API_KEY 配置"
    echo "是否覆盖？(y/n)"
    read -r OVERWRITE

    if [ "$OVERWRITE" = "y" ]; then
        # 删除旧的配置
        sed -i '' '/ZHIPUAI_API_KEY/d' "$CONFIG_FILE"
    else
        echo "❌ 取消配置"
        exit 0
    fi
fi

# 添加新配置
echo "" >> "$CONFIG_FILE"
echo "# 智谱AI API Key - Alpha Vision Pro" >> "$CONFIG_FILE"
echo "export ZHIPUAI_API_KEY=\"$API_KEY\"" >> "$CONFIG_FILE"

echo ""
echo "✅ 配置已添加到 $CONFIG_FILE"
echo ""
echo "请运行以下命令使配置生效:"
echo ""
echo "  source $CONFIG_FILE"
echo ""
echo "或者重新打开终端"
echo ""
echo "验证配置:"
echo "  echo \$ZHIPUAI_API_KEY"

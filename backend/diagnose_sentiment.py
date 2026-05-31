#!/usr/bin/env python3
"""
智谱AI配置诊断工具
用于诊断 ZHIPUAI_API_KEY 配置问题
"""

import os
import sys

def print_section(title):
    """打印分节标题"""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)

def check_env_variable():
    """检查环境变量"""
    print_section("1. 检查环境变量")

    zhipu_key = os.getenv("ZHIPUAI_API_KEY", "")
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")

    print(f"\nZHIPUAI_API_KEY: {'✅ 已设置' if zhipu_key else '❌ 未设置'}")
    if zhipu_key:
        print(f"   Key 长度: {len(zhipu_key)} 字符")
        if zhipu_key == "your_zhipuai_api_key_here":
            print("   ⚠️  警告: 使用的是示例 Key，请替换为真实 Key")
        elif len(zhipu_key) < 20:
            print("   ⚠️  警告: Key 长度太短，可能不正确")

    print(f"\nDEEPSEEK_API_KEY: {'✅ 已设置' if deepseek_key else '❌ 未设置'}")
    if deepseek_key:
        print("   ✅ 已配置非空 Key")

    return zhipu_key, deepseek_key

def check_dotenv():
    """检查 .env 文件"""
    print_section("2. 检查 .env 文件")

    env_paths = [
        ".env",
        "../.env",
    ]

    found = False
    for path in env_paths:
        if os.path.exists(path):
            print(f"\n✅ 找到配置文件: {path}")
            found = True

            with open(path, 'r') as f:
                content = f.read()
                if 'ZHIPUAI_API_KEY' in content:
                    print("   ✅ 文件中包含 ZHIPUAI_API_KEY")
                    for line in content.split('\n'):
                        if 'ZHIPUAI_API_KEY' in line and not line.strip().startswith('#'):
                            if '=' in line:
                                value = line.split('=')[1].strip()
                                if value == "your_zhipuai_api_key_here":
                                    print("   ⚠️  使用的是示例 Key")
                                elif value:
                                    print("   ✅ 已配置非空 Key")
                else:
                    print("   ⚠️  文件中未找到 ZHIPUAI_API_KEY")

    if not found:
        print("\n❌ 未找到 .env 文件")
        print("   提示: 运行 'cp .env.example .env' 创建配置文件")

    return found

def check_dependencies():
    """检查依赖包"""
    print_section("3. 检查依赖包")

    dependencies = {
        'requests': 'HTTP 请求库',
        'pydantic': '数据验证库',
    }

    optional_deps = {
        'dotenv': '环境变量加载（推荐安装）',
    }

    print("\n必需依赖:")
    for module, desc in dependencies.items():
        try:
            __import__(module)
            print(f"   ✅ {module}: {desc}")
        except ImportError:
            print(f"   ❌ {module}: {desc} (未安装)")

    print("\n可选依赖:")
    for module, desc in optional_deps.items():
        try:
            if module == 'dotenv':
                from dotenv import load_dotenv
                print(f"   ✅ python-dotenv: {desc}")
        except ImportError:
            print(f"   ⚠️  python-dotenv: {desc}")

    print("\n安装命令:")
    print("   pip install python-dotenv requests pydantic")

def check_sentiment_analyzer():
    """测试情绪分析器初始化"""
    print_section("4. 测试情绪分析器")

    try:
        from core.sentiment_analyzer import SentimentAnalyzer

        print("\n初始化分析器...")
        analyzer = SentimentAnalyzer()

        if analyzer.provider:
            print(f"✅ 成功初始化")
            print(f"   供应商: {analyzer.provider.upper()}")
            print(f"   模型: {analyzer.model}")
            print(f"   API URL: {analyzer.api_url}")
        else:
            print(f"⚠️  使用关键词匹配模式（未配置 API Key）")

        # 测试一条新闻
        print("\n测试新闻分析...")
        test_news = "贵州茅台股价大涨5%，创历史新高"
        result = analyzer.analyze_sentiment(test_news)

        if result:
            print(f"✅ 分析成功")
            print(f"   新闻: {test_news}")
            print(f"   情绪: {result.label}")
            print(f"   评分: {result.score}")
            print(f"   理由: {result.reason}")
            return True
        else:
            print(f"❌ 分析失败")
            return False

    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_api_connection():
    """测试 API 连接"""
    print_section("5. 测试智谱AI API 连接")

    zhipu_key = os.getenv("ZHIPUAI_API_KEY", "")
    if not zhipu_key or zhipu_key == "your_zhipuai_api_key_here":
        print("\n⚠️  未配置有效的 ZHIPUAI_API_KEY，跳过 API 测试")
        return False

    try:
        import requests
        import json

        print("\n发送测试请求...")
        url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        headers = {
            "Authorization": f"Bearer {zhipu_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "glm-4-flash",
            "messages": [{"role": "user", "content": "你好"}],
            "temperature": 0.3
        }

        response = requests.post(url, headers=headers, json=payload, timeout=10)

        if response.status_code == 200:
            print("✅ API 连接成功")
            result = response.json()
            if 'choices' in result:
                print(f"   响应: {result['choices'][0]['message']['content'][:50]}...")
            return True
        elif response.status_code == 401:
            print("❌ API Key 无效或未授权")
            print("   请检查 API Key 是否正确")
            return False
        else:
            print(f"❌ API 返回错误: {response.status_code}")
            print(f"   响应: {response.text[:200]}")
            return False

    except requests.exceptions.Timeout:
        print("❌ 连接超时")
        print("   请检查网络连接")
        return False
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return False

def main():
    """主诊断流程"""
    print("""
╔══════════════════════════════════════════════════════════╗
║     智谱AI配置诊断工具                                   ║
╚══════════════════════════════════════════════════════════╝
    """)

    # 1. 检查环境变量
    zhipu_key, deepseek_key = check_env_variable()

    # 2. 检查 .env 文件
    check_dotenv()

    # 3. 检查依赖
    check_dependencies()

    # 4. 测试分析器
    analyzer_ok = check_sentiment_analyzer()

    # 5. 测试 API 连接（如果有 Key）
    if zhipu_key and zhipu_key != "your_zhipuai_api_key_here":
        api_ok = check_api_connection()
    else:
        api_ok = None

    # 总结
    print_section("诊断总结")

    print("\n配置状态:")
    print(f"   环境变量: {'✅' if zhipu_key or deepseek_key else '⚠️  未设置'}")
    print(f"   分析器: {'✅' if analyzer_ok else '❌'}")
    print(f"   API连接: {'✅' if api_ok == True else '⚠️  跳过' if api_ok is None else '❌'}")

    if analyzer_ok:
        print("\n✅ 配置成功！情绪分析功能可以正常使用")
        print("\n下一步:")
        print("   1. 启动后端: PYTHONPATH=. python3 -m uvicorn api:app --reload")
        print("   2. 查看前端: 打开浏览器访问 http://localhost:3000")
        return 0
    else:
        print("\n❌ 配置存在问题，请根据上述提示修复")
        print("\n常见问题:")
        print("   1. API Key 未设置或错误")
        print("   2. 缺少依赖包")
        print("   3. 网络连接问题")
        return 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  诊断已中断")
        sys.exit(1)

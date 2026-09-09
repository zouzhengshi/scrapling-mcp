# -*- coding: utf-8 -*-
"""
Scrapling 双引擎抓取器 — 测试脚本。

用法:
    cd D:/Scrapling
    .\\scrapling_env\\Scripts\\python.exe test.py

验证：
    1. 引擎导入和实例化
    2. auto 模式（Crawl4AI 优先，Scrapling 兜底）
    3. 结构化结果格式
"""

import asyncio
import sys
import os

# 确保项目根目录在导入路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.engine import ScraplingEngine, ScrapeResult


async def main():
    print("=" * 60)
    print("  Scrapling - 双引擎网页抓取器 测试")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. 基本实例化
    # ------------------------------------------------------------------
    print("\n[1/3] 创建引擎实例...")
    engine = ScraplingEngine()
    print("  引擎创建成功。")

    # ------------------------------------------------------------------
    # 2. 测试 auto 模式抓取
    # ------------------------------------------------------------------
    print("\n[2/3] 测试抓取（auto 模式）...")
    url = "https://httpbin.org/html"
    result = await engine.scrape(url, mode="auto")

    # ------------------------------------------------------------------
    # 3. 验证结果结构
    # ------------------------------------------------------------------
    print("\n[3/3] 验证结果结构...")
    print(f"  URL:         {result.url}")
    print(f"  使用引擎:    {result.engine_used}")
    print(f"  成功:        {result.success}")
    print(f"  耗时:        {result.elapsed_ms:.0f} ms")
    print(f"  错误:        {result.error}")
    print(f"  Markdown 长度: {len(result.markdown)} 字符")
    print(f"  元数据:      {result.metadata}")

    # 显示预览
    if result.markdown:
        preview = result.markdown[:300]
        print(f"\n  --- Markdown 预览 ---")
        print(f"  {preview}...")
        print(f"  -----------------------")

    # 总结
    print("\n" + "=" * 60)
    if result.success:
        print("  通过: 引擎工作正常！")
    else:
        print(f"  提示: 抓取失败（{result.error}）。")
        print(f"  如果测试网址不可达，这是正常现象。")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 附加: 模块级便捷函数
    # ------------------------------------------------------------------
    print("\n[附加] 测试模块级 scrape()...")
    from src.engine import scrape
    result2 = await scrape("https://httpbin.org/html", mode="auto")
    print(f"  成功: {result2.success}, 引擎: {result2.engine_used}")


if __name__ == "__main__":
    asyncio.run(main())

# Third-party notices

## Utopia-V/codex-deepseek-subagent

`hooks/plaintext_handoff.py`、对应的回归测试以及 skill/handoff 设计源自：

- https://github.com/Utopia-V/codex-deepseek-subagent
- snapshot: `1377b7655ea98ed50a5131172b579b56ed744793`

这些文件在本仓库中针对 OpenCode Go 安装路径和说明进行了组合适配。

MIT License

Copyright (c) 2026 Utopia-V

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## goldtetsola/opencode-bridge

Responses→Chat transport behavior and compatibility tests were designed against:

- https://github.com/goldtetsola/opencode-bridge
- snapshot: `61c79e5c18a04448f02a472ea8734fd0a134c0fb`

The focused bridge in `src/codex_opencode_go_bridge/` is a reduced implementation
for one model and one behaviorally no-write lane. The complete MissionV1/runtime/certification
stack is not included. The upstream is Apache-2.0 licensed; this repository keeps
the same top-level license for a clear combined redistribution boundary.

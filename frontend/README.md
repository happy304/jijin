# 基金量化平台 - 前端

基于 React 18 + Vite + TypeScript 构建的基金量化研究平台前端。

## 技术栈

- **构建工具**: Vite 6
- **UI 框架**: React 18 + TypeScript
- **组件库**: Ant Design 5（中文友好）
- **状态管理**: 
  - TanStack Query（服务端状态）
  - Zustand（客户端状态）
- **路由**: React Router v6
- **HTTP 客户端**: Axios（含拦截器与统一错误处理）
- **代码质量**: ESLint + Prettier + TypeScript strict mode

## 项目结构

```
src/
├── api/          # API 客户端（axios 实例、拦截器）
├── components/   # 通用组件
│   └── layout/   # 布局组件（侧边栏、头部、内容区）
├── pages/        # 页面组件
├── routes/       # 路由配置
├── stores/       # Zustand 状态管理
├── styles/       # 全局样式
├── theme/        # Ant Design 主题配置
└── utils/        # 工具函数
```

## 开发

```bash
# 安装依赖
npm install

# 启动开发服务器（默认 http://localhost:5173）
npm run dev

# 类型检查
npm run type-check

# 代码检查
npm run lint

# 格式化
npm run format

# 构建生产版本
npm run build

# 预览生产构建
npm run preview
```

## 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `VITE_API_BASE_URL` | 后端 API 基础路径 | `/api` |

开发模式下，Vite 会将 `/api` 请求代理到 `http://localhost:8000`。

## Docker

生产构建使用 nginx 提供静态文件服务，并反向代理 `/api` 到后端容器。

开发模式通过 `docker-compose.dev.yml` 挂载源码目录并启动 Vite HMR 开发服务器。

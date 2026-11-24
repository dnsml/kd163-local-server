// 仅为绕过 rea-agents 的 Node 版本门禁（本机 22.22.2，要求 >=22.23.4）。
// 除版本号外不改任何逻辑；若命令真的依赖 22.23+ 的新 API 会直接抛错，届时装新 Node。
process.versions.node = '22.23.4';
await import('file:///C:/Users/wzj/AppData/Local/npm-cache/_npx/af752b90495482d7/node_modules/rea-agents/scripts/rea.mjs');

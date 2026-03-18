import express, { type Express } from "express";
import cors from "cors";
import { createProxyMiddleware } from "http-proxy-middleware";
import router from "./routes";

const app: Express = express();

const PYTHON_API_PORT = process.env.PYTHON_API_PORT || "5001";

app.use(
  "/api/py-api",
  createProxyMiddleware({
    target: `http://127.0.0.1:${PYTHON_API_PORT}`,
    changeOrigin: true,
    pathRewrite: { "^/api/py-api": "" },
    timeout: 0,
    proxyTimeout: 0,
    on: {
      proxyReq: (proxyReq) => {
        proxyReq.setSocketKeepAlive(true);
      },
    },
  }),
);

app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.use("/api", router);

export default app;

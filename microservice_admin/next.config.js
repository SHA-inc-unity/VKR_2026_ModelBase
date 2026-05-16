/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  eslint: {
    ignoreDuringBuilds: true,
  },
  basePath: '/admin',
  assetPrefix: '/admin',
  env: {
    NEXT_PUBLIC_BASE_PATH: '/admin',
  },
};

module.exports = nextConfig;

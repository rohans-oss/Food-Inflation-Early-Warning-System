/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone", // small Docker image (see Dockerfile)
  reactStrictMode: true,
};

export default nextConfig;

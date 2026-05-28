output "demo_runner_public_ip" {
  description = "Public IP of the demo runner ECS"
  value       = huaweicloud_vpc_eip.taurus-demo-eip.address
}

output "taurus_host" {
  description = "TaurusDB proxy endpoint (use this for app connections)"
  value       = huaweicloud_gaussdb_mysql_proxy.taurus-demo-proxy.address
}

output "taurus_port" {
  description = "TaurusDB proxy port"
  value       = huaweicloud_gaussdb_mysql_proxy.taurus-demo-proxy.port
}

output "taurus_instance_id" {
  description = "TaurusDB instance ID (for failover API calls)"
  value       = huaweicloud_gaussdb_mysql_instance.taurus-demo-db.id
}

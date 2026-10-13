removed {
  from = aws_s3_object.agent_html

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_s3_bucket_public_access_block.agent_frontend

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_s3_bucket_server_side_encryption_configuration.agent_frontend

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_s3_bucket_versioning.agent_frontend

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_s3_bucket.agent_frontend

  lifecycle {
    destroy = false
  }
}
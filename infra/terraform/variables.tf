// Переменные. Значения не заданы: файла terraform.tfvars в репозитории нет
// и не будет - идентификаторы образов и зон относятся к учётной записи
// Заказчика.

variable "zone" {
  description = "Зона доступности. Только регионы РФ: NFR-3.5."
  type        = string
  default     = "ru-central1-a"
}

variable "gpu_platform_id" {
  description = "Платформа с GPU. Требуется A100 40 ГБ, обоснование - ADR-0019."
  type        = string
}

variable "gpu_image_id" {
  description = "Образ с драйверами NVIDIA и CUDA."
  type        = string
}

variable "release" {
  description = <<-EOT
    Идентификатор выпуска из записи реестра, infra/release/.
    Передаётся в cloud-init и далее в приложение: становится меткой release
    на всех метриках. Без неё канарейку нельзя отличить от базы.
  EOT
  type        = string
  default     = "dev"
}
